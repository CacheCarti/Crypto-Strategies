from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 18,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.05
        self.cooldown_bars = 16
        self.max_hold_bars = 18
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.recovery_target = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100
        self.recovery_target = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            current_close = closes[-1]

            # Exit criteria: reached 24-bar crash midpoint or max holding duration reached
            if self.recovery_target > 0 and current_close >= self.recovery_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "reached_crash_midpoint_target",
                        "close": current_close,
                        "target": self.recovery_target,
                        "bars_held": bars_held,
                    },
                )
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_horizon_reached",
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Cooldown guard after recent exit
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Event Identification: 24-bar flush
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        curr_close = closes[-1]
        prev_high = highs[-2]

        if recent_high <= 0:
            return None

        drop_pct = (recent_high - recent_low) / recent_high
        is_flush = drop_pct >= self.min_drop_pct

        # Capitulation reversal trigger: current close confirms above previous bar's high
        is_reversal_candle = curr_close > prev_high and closes[-2] <= prev_high

        # Avoid entering if already recovered past the midpoint
        midpoint = recent_low + (recent_high - recent_low) * 0.50
        below_midpoint = curr_close < midpoint

        # Regime/crisis guard: Avoid entering in continuous MELTDOWN
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        not_prolonged_meltdown = regime != "MELTDOWN" and crisis_score < 0.85

        if is_flush and is_reversal_candle and below_midpoint and not_prolonged_meltdown:
            self.entry_bar = current_idx
            self.recovery_target = midpoint

            # Confidence scaled by depth of crash
            confidence = min(0.90, max(0.55, 0.50 + drop_pct * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=3600 * self.max_hold_bars,
                metadata={
                    "reason": "flush_capitulation_breakout",
                    "drop_pct": round(drop_pct * 100, 2),
                    "recent_high": recent_high,
                    "recent_low": recent_low,
                    "target_midpoint": round(midpoint, 2),
                    "close": curr_close,
                },
            )

        return None