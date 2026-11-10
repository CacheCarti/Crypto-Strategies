from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours average hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.05       # 5% flush threshold to ensure realistic sample size
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.midpoint_target = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check exit if in position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_price = ctx.bar.close

            # Target 1: Midpoint bounce of crash range
            if self.midpoint_target > 0 and curr_price >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "price": curr_price, "target": self.midpoint_target}
                )

            # Target 2: Time-based exit after 16 hours to prevent dead capital
            if bars_held >= 16:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_recovery_hold_reached", "bars_held": bars_held}
                )

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entry during extreme prolonged crisis regimes (meltdown failure mode from prior evidence)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Flush calculation: percentage drop over last 24 bars
        recent_max_high = max(highs[-self.lookback:])
        recent_min_low = min(lows[-self.lookback:])
        current_close = ctx.bar.close

        total_drop = (recent_max_high - recent_min_low) / recent_max_high
        flush_occurred = total_drop >= self.drop_thresh

        if not flush_occurred:
            return None

        # Capitulation reversal trigger: current close breaks above previous bar high
        prev_high = highs[-2]
        if current_close > prev_high:
            self.midpoint_target = (recent_max_high + recent_min_low) / 2.0
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, 0.5 + (total_drop * 4.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "flush_capitulation_reversal",
                    "total_drop_pct": round(total_drop * 100, 2),
                    "prev_high": prev_high,
                    "close": current_close,
                    "midpoint_target": round(self.midpoint_target, 2),
                    "crisis_score": crisis_score,
                }
            )

        return None