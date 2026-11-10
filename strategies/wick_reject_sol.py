from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RejectionWickStrategy(Strategy):
    METADATA = {
        "name": "RejectionWickStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # 1 hour bar, aiming for ~1 bar hold
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_ratio_threshold = 0.6  # Wick must be 60% of total bar range
        self.volume_ma_period = 20
        self.volume_multiplier = 1.5   # Current volume must be 1.5x average
        self.cooldown_bars = 5       # Wait 5 bars after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for existing position
        if ctx.has_position():
            # If we have a position, we only look to exit based on time or profit/loss.
            # The engine handles declared SL/TP. We can add more complex exit logic here if needed,
            # but for this strategy, we rely on the declared SL/TP and the implicit horizon.
            # No additional exit signal needed unless we want to override SL/TP.
            return ctx.signal("hold")

        # Get current bar data
        current_open = opens[-1]
        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        current_volume = volumes[-1]

        # Get previous bar data for confirmation
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]

        # Calculate average volume
        avg_volume = self._sma(volumes[:-1], self.volume_ma_period)
        if avg_volume is None:
            return None

        # Check for high volume
        if current_volume < avg_volume * self.volume_multiplier:
            return None

        # Calculate bar range
        prev_range = prev_high - prev_low
        if prev_range == 0:
            return None

        # Check for large upper wick (bearish rejection) on previous bar
        upper_wick = prev_high - max(prev_open, prev_close)
        is_large_upper_wick = upper_wick / prev_range >= self.wick_ratio_threshold

        # Check for large lower wick (bullish rejection) on previous bar
        lower_wick = min(prev_open, prev_close) - prev_low
        is_large_lower_wick = lower_wick / prev_range >= self.wick_ratio_threshold

        signal_direction = None
        reason = ""
        entry_price = current_close
        confidence = 0.0

        # Bearish Rejection (large upper wick)
        if is_large_upper_wick:
            # Confirmation: current bar closes below the previous close
            if current_close < prev_close:
                signal_direction = "short"
                reason = "bearish_rejection_wick_confirmed"
                # Target partial retracement of the previous bar's range
                # Or a tight stop above the previous high.
                # For this strategy, we'll use declared SL/TP and let the engine manage.
                confidence = 0.7 # Higher confidence for confirmed rejection

        # Bullish Rejection (large lower wick)
        elif is_large_lower_wick:
            # Confirmation: current bar closes above the previous close
            if current_close > prev_close:
                signal_direction = "long"
                reason = "bullish_rejection_wick_confirmed"
                # Target partial retracement of the previous bar's range
                # Or a tight stop below the previous low.
                confidence = 0.7 # Higher confidence for confirmed rejection

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "prev_open": prev_open,
                    "prev_close": prev_close,
                    "prev_high": prev_high,
                    "prev_low": prev_low,
                    "prev_range": prev_range,
                    "upper_wick_ratio": upper_wick / prev_range if prev_range > 0 else 0,
                    "lower_wick_ratio": lower_wick / prev_range if prev_range > 0 else 0,
                    "current_close": current_close,
                    "current_volume": current_volume,
                    "avg_volume_20": avg_volume,
                    "volume_multiplier": self.volume_multiplier,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this exploration.
        pass