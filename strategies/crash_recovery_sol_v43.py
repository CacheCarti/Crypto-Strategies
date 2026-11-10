from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065  # 6.5% drop threshold (calibrated for high conviction without starvation)
        self.cooldown_bars = 16    # Explicit event separation
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.panic_low = 0.0
        self.flush_high = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position Management: Exit logic
        if ctx.has_position() and ctx.position_direction() == "long":
            curr_c = ctx.bar.close
            bars_held = ctx.bar_index - self.entry_bar

            # Midpoint target exit from the flush range
            target_price = (self.panic_low + self.flush_high) / 2.0 if self.flush_high > self.panic_low else 0.0

            if target_price > 0 and curr_c >= target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "target_flush_midpoint_reached", "close": curr_c, "midpoint": target_price}
                )

            # Hard structural invalidation if price breaks beneath the panic low
            if self.panic_low > 0 and curr_c < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "panic_low_violation", "close": curr_c, "panic_low": self.panic_low}
                )

            # Time stop to prevent capital tie-up
            if bars_held >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held}
                )

            return None

        # If already holding another direction or cooling down, hold off
        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market health filter: avoid structural meltdowns where knife-catching is fatal
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Measure 24-bar flush
        ret_24 = (closes[-1] - closes[-25]) / closes[-25]
        if ret_24 > self.drop_thresh:
            return None

        # Stabilization confirmation: current bar close breaks previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Supplementary oversold filter
        rsi_val = self._rsi(closes, period=14)
        if rsi_val > 42.0:
            return None

        # Establish event boundaries
        local_lows = lows[-self.lookback:]
        local_highs = highs[-self.lookback:]
        self.panic_low = min(local_lows)
        self.flush_high = max(local_highs)
        self.entry_bar = ctx.bar_index

        # Dynamic Stop Loss based on panic low distance
        dist_to_low_bps = ((curr_close - self.panic_low) / curr_close) * 10000.0
        sl_bps = max(250.0, min(dist_to_low_bps + 40.0, 500.0))

        # Dynamic Take Profit towards the midpoint of the panic range
        midpoint = (self.panic_low + self.flush_high) / 2.0
        dist_to_mid_bps = ((midpoint - curr_close) / curr_close) * 10000.0 if midpoint > curr_close else 400.0
        tp_bps = max(350.0, min(dist_to_mid_bps, 650.0))

        # Confidence scaled by magnitude of oversold condition
        conf = 0.65
        if rsi_val < 30.0:
            conf += 0.15
        if ret_24 < -0.10:
            conf += 0.10
        conf = min(conf, 0.95)

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=18000,
            metadata={
                "reason": "flush_stabilization_reversal",
                "ret_24": ret_24,
                "rsi": rsi_val,
                "panic_low": self.panic_low,
                "flush_high": self.flush_high,
                "entry_price": curr_close,
                "prev_high": prev_high
            }
        )