from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.065  # 6.5% drop over 24h
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0
        self.stop_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0
        self.stop_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        curr_close = ctx.bar.close
        curr_high = ctx.bar.high
        curr_low = ctx.bar.low

        # Manage open position
        if ctx.has_position():
            # Exit 1: Target flush midpoint reached
            if self.target_midpoint > 0 and curr_close >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "price": curr_close,
                    "target": self.target_midpoint
                })

            # Exit 2: Dynamic stop below the panic low
            if self.stop_price > 0 and curr_close < self.stop_price:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "stop_below_panic_low",
                    "price": curr_close,
                    "stop_price": self.stop_price
                })

            # Exit 3: Max hold horizon protection (18 bars ~ 18 hours)
            if ctx.bar_index - self.entry_bar >= 18:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_decay_exit",
                    "bars_held": ctx.bar_index - self.entry_bar,
                    "price": curr_close
                })

            return None

        # Check cooldown after prior exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 1. Measure rolling flush over 24 bars
        anchor_close = closes[-self.lookback - 1]
        rolling_return = (curr_close - anchor_close) / anchor_close

        # Require a steep flush
        if rolling_return > self.flush_threshold_pct:
            return None

        # 2. Check for stabilization: Close above previous bar's high
        prev_high = highs[-2]
        if curr_close <= prev_high:
            return None

        # 3. Indicator confirmation: Oversold or recovering RSI (< 42)
        rsi_val = self._rsi(closes, 14)
        if rsi_val > 42.0:
            return None

        # Macro sentiment filter: avoid absolute meltdown trap if F&G is at bedrock extreme (< 10)
        fg_index = ctx.features.get("fear_greed_index", 50)
        if fg_index < 10:
            return None

        # Compute dynamic range midpoint and panic low stop
        panic_low = min(lows[-(self.lookback + 1):])
        flush_high = max(highs[-(self.lookback + 1):])
        midpoint = (flush_high + panic_low) / 2.0

        # Safety check: ensure midpoint offers positive expected payoff
        if midpoint <= curr_close:
            midpoint = curr_close * 1.035

        # Distance to panic low
        sl_pct = (curr_close - panic_low) / curr_close
        sl_bps = max(180.0, min(500.0, sl_pct * 10000.0 * 1.15))
        tp_bps = max(250.0, min(650.0, ((midpoint - curr_close) / curr_close) * 10000.0))

        self.target_midpoint = midpoint
        self.stop_price = panic_low * 0.995
        self.entry_bar = ctx.bar_index

        conf = min(0.90, max(0.60, abs(rolling_return) * 6.0))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_stabilization_rebound",
                "drop_24h_pct": round(rolling_return * 100, 2),
                "rsi": round(rsi_val, 2),
                "panic_low": panic_low,
                "target_midpoint": round(midpoint, 2),
                "fear_greed": fg_index
            }
        )