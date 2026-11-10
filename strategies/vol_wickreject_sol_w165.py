from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRejectionFade(Strategy):
    METADATA = {
        "name": "VolatilityRejectionFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.wick_threshold_pct = 0.015 # Wick must be at least 1.5% of candle range
        self.volume_ratio_threshold = 1.5 # Volume must be 1.5x average
        self.body_ratio_threshold = 0.3 # Body must be small relative to candle range
        self.rejection_confirm_threshold_pct = 0.005 # Next bar confirms rejection by moving against wick direction

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate average volume for comparison
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else volumes[-1]

        # Check for previous bar's "unusually large high-volume rejection wick"
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        prev_candle_range = prev_high - prev_low
        prev_body_size = abs(prev_close - prev_open)

        if prev_candle_range == 0:
            return None # Avoid division by zero

        # Check for large wick and small body
        is_long_wick_top = (prev_high - max(prev_open, prev_close)) / prev_candle_range > self.wick_threshold_pct
        is_long_wick_bottom = (min(prev_open, prev_close) - prev_low) / prev_candle_range > self.wick_threshold_pct
        is_small_body = prev_body_size / prev_candle_range < self.body_ratio_threshold

        # Check for high volume
        is_high_volume = prev_volume > avg_volume * self.volume_ratio_threshold

        # Check for confirmation on current bar
        current_open = opens[-1]
        current_close = closes[-1]
        current_bar_price_change = current_close - current_open

        signal_direction: Optional[str] = None
        rejection_type: str = ""

        # Fade a top wick (short signal)
        if is_long_wick_top and is_small_body and is_high_volume:
            # Current bar confirms rejection if it moves down
            if current_bar_price_change < -self.rejection_confirm_threshold_pct * current_open:
                if not ctx.has_position():
                    signal_direction = "short"
                    rejection_type = "top_wick_rejection"
                elif ctx.position_direction() == "long":
                    # Flip long to short
                    signal_direction = "short"
                    rejection_type = "top_wick_rejection_flip_long"

        # Fade a bottom wick (long signal)
        elif is_long_wick_bottom and is_small_body and is_high_volume:
            # Current bar confirms rejection if it moves up
            if current_bar_price_change > self.rejection_confirm_threshold_pct * current_open:
                if not ctx.has_position():
                    signal_direction = "long"
                    rejection_type = "bottom_wick_rejection"
                elif ctx.position_direction() == "short":
                    # Flip short to long
                    signal_direction = "long"
                    rejection_type = "bottom_wick_rejection_flip_short"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.75,
                metadata={
                    "reason": rejection_type,
                    "prev_bar_high": prev_high,
                    "prev_bar_low": prev_low,
                    "prev_bar_open": prev_open,
                    "prev_bar_close": prev_close,
                    "prev_bar_volume": prev_volume,
                    "current_bar_open": current_open,
                    "current_bar_close": current_close,
                    "wick_threshold_pct": self.wick_threshold_pct,
                    "volume_ratio_threshold": self.volume_ratio_threshold,
                    "body_ratio_threshold": self.body_ratio_threshold,
                    "rejection_confirm_threshold_pct": self.rejection_confirm_threshold_pct,
                }
            )

        # Exit logic: If we are in a position and a strong counter-rejection wick appears
        if ctx.has_position():
            current_open = opens[-1]
            current_close = closes[-1]
            current_high = highs[-1]
            current_low = lows[-1]
            current_volume = volumes[-1]

            current_candle_range = current_high - current_low
            current_body_size = abs(current_close - current_open)

            if current_candle_range == 0:
                return None

            is_long_wick_top_curr = (current_high - max(current_open, current_close)) / current_candle_range > self.wick_threshold_pct
            is_long_wick_bottom_curr = (min(current_open, current_close) - current_low) / current_candle_range > self.wick_threshold_pct
            is_small_body_curr = current_body_size / current_candle_range < self.body_ratio_threshold
            is_high_volume_curr = current_volume > avg_volume * self.volume_ratio_threshold

            if ctx.position_direction() == "long" and is_long_wick_top_curr and is_small_body_curr and is_high_volume_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_long_counter_top_wick_rejection",
                        "current_bar_high": current_high,
                        "current_bar_low": current_low,
                        "current_bar_open": current_open,
                        "current_bar_close": current_close,
                        "current_bar_volume": current_volume,
                    }
                )
            elif ctx.position_direction() == "short" and is_long_wick_bottom_curr and is_small_body_curr and is_high_volume_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_short_counter_bottom_wick_rejection",
                        "current_bar_high": current_high,
                        "current_bar_low": current_low,
                        "current_bar_open": current_open,
                        "current_bar_close": current_close,
                        "current_bar_volume": current_volume,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for this strategy on position close, just reset cooldown.
        # The cooldown is handled in on_bar.
        pass