from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_high = 0.0
        self.panic_low = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        current_high = ctx.bar.high

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Target flush range midpoint
            target_midpoint = (self.panic_high + self.panic_low) / 2.0
            
            if current_close >= target_midpoint and bars_held >= 2:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "close": current_close,
                        "target_midpoint": target_midpoint,
                        "bars_held": bars_held
                    }
                )

            # Trailing dynamic stop below panic low
            if current_close < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_violated_stop",
                        "close": current_close,
                        "panic_low": self.panic_low,
                        "bars_held": bars_held
                    }
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check for 7%+ drop over lookback
        price_n_ago = closes[0]
        recent_drop = (closes[-2] - price_n_ago) / price_n_ago

        if recent_drop <= self.drop_thresh:
            prev_high = highs[-2]
            prev_close = closes[-2]
            prev_low = lows[-2]

            # Stabilization trigger: closed above previous bar's high
            if current_close > prev_high:
                window_highs = highs[-self.lookback:]
                window_lows = lows[-self.lookback:]
                self.panic_high = max(window_highs)
                self.panic_low = min(window_lows)

                # Ensure valid flush structure
                flush_range = self.panic_high - self.panic_low
                if flush_range <= 0:
                    return None

                # Distance to panic low defines SL in bps (bounded to prevent excessive risk)
                sl_dist = max(current_close - self.panic_low, current_close * 0.02)
                sl_bps = min(max((sl_dist / current_close) * 10000.0, 150.0), 500.0)

                # Distance to midpoint defines TP in bps
                midpoint = (self.panic_high + self.panic_low) / 2.0
                tp_dist = max(midpoint - current_close, current_close * 0.025)
                tp_bps = min(max((tp_dist / current_close) * 10000.0, 200.0), 650.0)

                self.entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=0.85,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "panic_drop_stabilization_breakout",
                        "drop_pct": recent_drop * 100.0,
                        "panic_high": self.panic_high,
                        "panic_low": self.panic_low,
                        "midpoint": midpoint,
                        "current_close": current_close,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps
                    }
                )

        return None