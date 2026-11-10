from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.045
        self.cooldown_period = 12
        self.max_hold_bars = 20
        self.bars_since_exit = 20
        self.entry_bar_idx = 0
        self.target_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        regime = ctx.market.get("regime", "NORMAL")

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_exhaustion",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        if self.bars_since_exit < self.cooldown_period:
            return None

        if regime == "MELTDOWN":
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        window_high = max(recent_highs)
        window_low = min(recent_lows)

        if window_high <= 0:
            return None

        drop_pct = (window_high - current_close) / window_high

        # 1. Significant flush over the rolling 24 bars
        # 2. Stabilization confirmation: current close breaks above previous bar high
        # 3. Bar is bullish (close > open)
        if drop_pct >= self.min_drop_pct and current_close > prev_high and current_close > ctx.bar.open:
            self.target_midpoint = (window_high + window_low) / 2.0
            
            # Ensure the midpoint recovery offers positive expected edge vs entry
            if self.target_midpoint <= current_close * 1.006:
                return None

            self.entry_bar_idx = ctx.bar_index
            drop_mag_bps = drop_pct * 10000.0
            confidence = min(0.9, max(0.55, 0.55 + (drop_pct - self.min_drop_pct) * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_reversal",
                    "drop_bps": round(drop_mag_bps, 1),
                    "flush_high": round(window_high, 2),
                    "flush_low": round(window_low, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "prev_high": round(prev_high, 2),
                    "price": current_close
                }
            )

        return None