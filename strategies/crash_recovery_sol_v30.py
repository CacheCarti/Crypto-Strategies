from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing target
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.07  # -7% flush threshold
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
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
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing position
        if ctx.has_position():
            curr_close = ctx.bar.close
            # Check midpoint take-profit target exit
            if self.target_midpoint > 0.0 and curr_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "close": curr_close,
                        "midpoint": self.target_midpoint,
                        "panic_low": self.panic_low
                    }
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate flush drop over the last 24 bars
        anchor_close = closes[-self.lookback]
        prior_close = closes[-2]
        rolling_return = (prior_close - anchor_close) / anchor_close

        # Require a severe flush (>7% drop over 24 bars)
        if rolling_return > self.drop_thresh:
            return None

        # Reversal trigger: First stabilization bar closing above previous bar's high
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        is_break_above_prev_high = (curr_close > prev_high)
        prev_bar_was_below = (closes[-2] <= highs[-3])

        if not (is_break_above_prev_high and prev_bar_was_below):
            return None

        # Check market oversold status
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi > 42.0:
            return None

        # Avoid extreme meltdown crisis conditions where falling knives continue
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Measure flush range
        panic_slice_lows = lows[-self.lookback:]
        panic_slice_highs = highs[-self.lookback:]
        p_low = min(panic_slice_lows)
        p_high = max(panic_slice_highs)

        # Ensure sensible range geometry
        if p_high <= p_low:
            return None

        midpoint = p_low + 0.5 * (p_high - p_low)
        # Midpoint must offer at least +1.2% upside to justify the entry
        if midpoint <= curr_close * 1.012:
            return None

        # Dynamic stop under panic low, capped by declared bounds
        sl_bps = min(500.0, max(250.0, ((curr_close - p_low) / curr_close) * 10000.0 + 35.0))
        tp_bps = min(600.0, max(300.0, ((midpoint - curr_close) / curr_close) * 10000.0))

        self.panic_low = p_low
        self.panic_high = p_high
        self.target_midpoint = midpoint

        # Confidence scaled by magnitude of oversold exhaustion
        confidence = min(0.9, max(0.6, 0.6 + (35.0 - rsi) * 0.01))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_reversal_stabilization",
                "rolling_return_24b": round(rolling_return, 4),
                "rsi": round(rsi, 2),
                "panic_low": round(p_low, 2),
                "panic_high": round(p_high, 2),
                "target_midpoint": round(midpoint, 2),
                "sl_bps": round(sl_bps, 1),
                "tp_bps": round(tp_bps, 1)
            }
        )