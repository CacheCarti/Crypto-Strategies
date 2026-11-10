from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # 12-hour nominal recovery target
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_flush = 24
        self.flush_threshold_pct = 0.055  # 5.5% drop over 24h
        self.cooldown_bars = 10
        self.max_hold_bars = 18
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_flush + 2)
        highs = ctx.highs(self.lookback_flush + 2)
        lows = ctx.lows(self.lookback_flush + 2)

        if len(closes) < self.lookback_flush + 2:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            curr_close = closes[-1]
            
            # Target 1: Midpoint recovery of the 24-bar flush range
            if self.flush_midpoint > 0.0 and curr_close >= self.flush_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_flush_midpoint_reached",
                        "price": curr_close,
                        "flush_midpoint": self.flush_midpoint,
                        "bars_held": bars_in_trade
                    }
                )

            # Target 2: Time-based decay exit
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_reached",
                        "price": curr_close,
                        "bars_held": bars_in_trade
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Flush Detection: High of 24 bars ago to current Low
        prior_window_high = max(highs[-self.lookback_flush:])
        curr_low = lows[-1]
        flush_pct = (prior_window_high - curr_low) / prior_window_high

        if flush_pct < self.flush_threshold_pct:
            return None

        # Market Context & Cross-Regime Confirmation (avoid repeating failed simple candle reversal)
        # 1. Require elevated volatility or extreme fear context (flush must occur in true panic)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        is_panic = (fear_greed <= 35) or (crisis_score > 0.25) or (regime in ("HIGH_VOL", "CRISIS", "MELTDOWN"))
        if not is_panic:
            return None

        # Capitulation candle confirmation:
        # Current bar closes back strictly above the prior bar's high, confirming buying absorption
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Calculate midpoint of the 24h selloff range to lock in profits
        lowest_in_flush = min(lows[-self.lookback_flush:])
        self.flush_midpoint = (prior_window_high + lowest_in_flush) / 2.0
        self.entry_bar = ctx.bar_index

        # Confidence scaled by depth of crash
        confidence = min(0.9, 0.5 + (flush_pct - self.flush_threshold_pct) * 5.0)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "capitulation_engulf_flush_reversal",
                "flush_pct": round(flush_pct * 100.0, 2),
                "flush_midpoint": round(self.flush_midpoint, 2),
                "fear_greed": fear_greed,
                "regime": regime,
                "price": curr_close
            }
        )