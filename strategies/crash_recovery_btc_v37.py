from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # 12 hours intended horizon
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.055  # 5.5% drop over 24h window
        self.max_hold_bars = 16   # Time-based stop
        self.cooldown_bars = 12   # Enforce cooldown after any exit
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.entry_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check existing position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close

            # Target 1: Reached 24-bar midpoint recovery
            if curr_close >= self.entry_midpoint and self.entry_midpoint > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_recovery_reached",
                        "curr_close": curr_close,
                        "midpoint": self.entry_midpoint,
                        "bars_held": bars_held,
                    }
                )

            # Target 2: Max time hold expired
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_expired",
                        "bars_held": bars_held,
                        "curr_close": curr_close,
                    }
                )

            return None

        # Cooldown guard: Avoid re-entering during persistent waterfall capitulations
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime & Macro Filter: Do not enter in total meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Drop calculation: Rolling drop over lookback window
        window_high = max(highs[-self.lookback:])
        curr_close = ctx.bar.close
        drop_pct = (window_high - curr_close) / window_high

        # Confirm panic flush condition
        if drop_pct < self.drop_thresh:
            return None

        # Capitulation reversal trigger: Current close breaks above previous bar high
        prev_high = highs[-2]
        if curr_close <= prev_high:
            return None

        # Cross-asset / Sentiment confirmation: Extreme fear or panic conditions
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        # Avoid fading when sentiment is still complacent; require some degree of caution/fear
        if fear_greed > 55.0:
            return None

        # Volatility confirmation: Reversal bar body must show solid commitment
        curr_open = ctx.bar.open
        if curr_close <= curr_open:
            return None

        # Midpoint of the 24-bar drop channel
        window_low = min(lows[-self.lookback:])
        midpoint = window_low + (window_high - window_low) * 0.50

        self.entry_bar = ctx.bar_index
        self.entry_midpoint = midpoint

        # Scale confidence based on drop severity
        confidence = min(0.9, 0.55 + (drop_pct - self.drop_thresh) * 4.0)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=320.0,
            take_profit_bps=480.0,
            horizon_seconds=43200,
            metadata={
                "reason": "capitulation_break_above_prior_high",
                "drop_pct": round(drop_pct, 4),
                "fear_greed": fear_greed,
                "prev_high": prev_high,
                "midpoint_target": round(midpoint, 2),
                "regime": regime,
            }
        )