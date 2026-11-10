from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RejectionWickStrategy(Strategy):
    METADATA = {
        "name": "RejectionWickSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough bars for ATR and volume calculations
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_period = 1  # Look at the previous bar for wick detection
        self.atr_period = 14
        self.volume_period = 20
        self.min_wick_ratio = 1.5  # Wick should be at least X times the body
        self.min_wick_to_range_ratio = 0.4 # Wick should be at least X% of the total range
        self.min_volume_multiplier = 1.5 # Volume should be X times average
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.atr_period + self.wick_period)
        highs = ctx.highs(self.atr_period + self.wick_period)
        lows = ctx.lows(self.atr_period + self.wick_period)
        volumes = ctx.volumes(self.volume_period + self.wick_period)

        if None in [closes, highs, lows, volumes]:
            return None

        # Ensure we have enough data for all calculations
        if len(closes) < self.atr_period + 1 or len(volumes) < self.volume_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        prev_bar_close = closes[-2]
        prev_bar_open = ctx.opens(2)[-2]
        prev_bar_high = highs[-2]
        prev_bar_low = lows[-2]
        prev_bar_volume = volumes[-2]

        current_bar_close = closes[-1]
        current_bar_open = ctx.opens(1)[-1]
        current_bar_high = highs[-1]
        current_bar_low = lows[-1]

        avg_volume = self._sma(volumes[:-1], self.volume_period) # Average volume excluding current bar
        if avg_volume is None:
            return None

        # Calculate wick and body sizes for the previous bar
        body = abs(prev_bar_close - prev_bar_open)
        upper_wick = prev_bar_high - max(prev_bar_open, prev_bar_close)
        lower_wick = min(prev_bar_open, prev_bar_close) - prev_bar_low
        bar_range = prev_bar_high - prev_bar_low

        signal_direction = None
        wick_type = None
        confidence = 0.0

        # Check for rejection wick pattern on the previous bar
        # Large upper wick + high volume -> potential short
        if upper_wick > body * self.min_wick_ratio and \
           upper_wick > bar_range * self.min_wick_to_range_ratio and \
           prev_bar_volume > avg_volume * self.min_volume_multiplier:
            # Confirmation: current bar closes below the previous bar's open
            if current_bar_close < prev_bar_open:
                signal_direction = "short"
                wick_type = "upper_rejection"
                confidence = 0.7
                # Increase confidence if current bar also has a bearish close (below its open)
                if current_bar_close < current_bar_open:
                    confidence += 0.1

        # Large lower wick + high volume -> potential long
        elif lower_wick > body * self.min_wick_ratio and \
             lower_wick > bar_range * self.min_wick_to_range_ratio and \
             prev_bar_volume > avg_volume * self.min_volume_multiplier:
            # Confirmation: current bar closes above the previous bar's open
            if current_bar_close > prev_bar_open:
                signal_direction = "long"
                wick_type = "lower_rejection"
                confidence = 0.7
                # Increase confidence if current bar also has a bullish close (above its open)
                if current_bar_close > current_bar_open:
                    confidence += 0.1

        if signal_direction and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence), # Cap confidence at 1.0
                metadata={
                    "reason": f"{wick_type}_confirmation",
                    "prev_bar_upper_wick": upper_wick,
                    "prev_bar_lower_wick": lower_wick,
                    "prev_bar_body": body,
                    "prev_bar_volume": prev_bar_volume,
                    "avg_volume": avg_volume,
                    "current_bar_close": current_bar_close,
                    "prev_bar_open": prev_bar_open
                }
            )

        # Exit logic: if we have a position, consider exiting
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Partial wick retracement target (e.g., 50% of the rejection wick)
            # For a long, if price reverses below 50% of the lower wick
            # For a short, if price reverses above 50% of the upper wick

            # For a long position (entered on lower wick rejection)
            if direction == "long" and wick_type == "lower_rejection": # This wick_type is from the previous bar that triggered, not necessarily the current bar
                # Simple exit: if current bar closes below its open (bearish) and below entry
                if current_bar_close < current_bar_open and current_bar_close < entry_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_wick_rejection_bearish_close_below_entry",
                            "current_bar_close": current_bar_close,
                            "current_bar_open": current_bar_open,
                            "entry_price": entry_price
                        }
                    )
            # For a short position (entered on upper wick rejection)
            elif direction == "short" and wick_type == "upper_rejection": # This wick_type is from the previous bar that triggered, not necessarily the current bar
                # Simple exit: if current bar closes above its open (bullish) and above entry
                if current_bar_close > current_bar_open and current_bar_close > entry_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_wick_rejection_bullish_close_above_entry",
                            "current_bar_close": current_bar_close,
                            "current_bar_open": current_bar_open,
                            "entry_price": entry_price
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_signal_bar = ctx.bar_index
        # You could log position details or adapt parameters here
        pass