from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold on 1h bars
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065       # -6.5% drop over 24h
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        has_pos = ctx.has_position()

        # Manage existing long position
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            
            # Midpoint take-profit or time exhaustion
            hit_target = self.target_midpoint > 0.0 and current_close >= self.target_midpoint
            stop_breached = self.panic_low > 0.0 and current_close < self.panic_low
            time_exhausted = bars_held >= 16

            if hit_target or stop_breached or time_exhausted:
                exit_reason = (
                    "midpoint_target_hit" if hit_target else
                    ("panic_low_violated" if stop_breached else "time_decay_exit")
                )
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "close": current_close,
                        "target_midpoint": self.target_midpoint,
                        "panic_low": self.panic_low,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Check cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar drop
        ref_close = closes[- (self.lookback + 1)]
        roc_24 = (current_close - ref_close) / ref_close

        # Recovery confirmation logic:
        # 1. 24-bar drop of at least 6.5%
        # 2. Reversal trigger: current close breaks above previous bar high
        # 3. RSI is not already overheated (> 55) after the bounce
        prev_high = highs[-2]
        prev_low = lows[-2]

        if roc_24 <= self.drop_thresh and current_close > prev_high:
            recent_lows = lows[-self.lookback:]
            recent_highs = highs[-self.lookback:]
            p_low = min(recent_lows)
            p_high = max(recent_highs)

            # Measure dynamic stop and target
            sl_pct = (current_close - p_low) / current_close
            sl_bps = max(180.0, min(500.0, sl_pct * 10000.0 + 30.0))
            
            midpoint = (p_low + p_high) / 2.0
            tp_pct = (midpoint - current_close) / current_close if midpoint > current_close else 0.03
            tp_bps = max(200.0, min(650.0, tp_pct * 10000.0))

            rsi_val = self._rsi(closes, period=14)
            if rsi_val <= 55.0:
                self.entry_bar = ctx.bar_index
                self.panic_low = p_low
                self.panic_high = p_high
                self.target_midpoint = midpoint

                # Confidence scaled by depth of crash
                conviction = min(0.9, max(0.45, 0.5 + abs(roc_24) * 2.0))

                return ctx.signal(
                    "long",
                    confidence=conviction,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "sol_panic_reversal_confirmation",
                        "roc_24": round(roc_24, 4),
                        "rsi": round(rsi_val, 2),
                        "panic_low": round(p_low, 2),
                        "target_midpoint": round(midpoint, 2),
                        "close": current_close,
                        "prev_high": prev_high,
                    }
                )

        return None