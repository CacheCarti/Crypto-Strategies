from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicCapitulationRecovery(Strategy):
    METADATA = {
        "name": "PanicCapitulationRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 28800,  # 8 hours typical recovery hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.crash_threshold_pct = 0.055  # ~5.5% drop in 24h
        self.cooldown_bars = 10
        self.max_hold_bars = 12
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

        current_close = ctx.bar.close
        prev_high = highs[-2]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            
            # Midpoint take profit or time exit
            if current_close >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_target_hit",
                        "close": current_close,
                        "target": self.midpoint_target,
                        "bars_held": bars_in_pos
                    }
                )
            if bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_expired",
                        "close": current_close,
                        "bars_held": bars_in_pos
                    }
                )
            return None

        # Mandatory cooldown guard
        if bars_since_exit < self.cooldown_bars:
            return None

        # Calculate 24-bar high/low and flush drop
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])

        if window_high <= 0:
            return None

        drawdown_pct = (window_high - window_low) / window_high

        # Detect flush and immediate reversal trigger
        # 1. 24h high-to-low flush >= 5.5%
        # 2. Capitulation bar: current close breaks strictly above previous bar's high
        # 3. Bar is bullish (close > open)
        is_flush = drawdown_pct >= self.crash_threshold_pct
        capitulation_break = current_close > prev_high
        is_bullish_bar = ctx.bar.close > ctx.bar.open

        if is_flush and capitulation_break and is_bullish_bar:
            # 24-bar range midpoint as structural target
            self.midpoint_target = window_low + (window_high - window_low) * 0.50
            
            # Only enter if there's meaningful edge to the midpoint target (> 120 bps)
            distance_to_target_bps = ((self.midpoint_target - current_close) / current_close) * 10000.0
            if distance_to_target_bps < 120.0:
                return None

            self.entry_bar = ctx.bar_index
            confidence = min(1.0, max(0.4, 0.4 + (drawdown_pct - self.crash_threshold_pct) * 5.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_capitulation_reversal",
                    "drawdown_pct": round(drawdown_pct, 4),
                    "midpoint_target": round(self.midpoint_target, 2),
                    "target_bps": round(distance_to_target_bps, 1),
                    "prev_high": prev_high,
                    "close": current_close
                }
            )

        return None