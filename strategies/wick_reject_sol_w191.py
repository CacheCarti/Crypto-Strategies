from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RejectionWickStrategy(Strategy):
    METADATA = {
        "name": "RejectionWickStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_ratio_threshold = 0.6  # Wick must be 60% of total bar range
        self.volume_ma_period = 20      # Period for average volume
        self.confirmation_bars = 1      # How many bars to wait for confirmation
        self.cooldown_bars = 5          # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_bar_volume = ctx.bar.volume
        avg_volume = self._sma(volumes[:-1], self.volume_ma_period)

        if avg_volume is None or current_bar_volume < avg_volume * 1.5: # 1.5x average volume
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        bar_range = current_high - current_low
        if bar_range == 0:
            return None

        # Check for large upper wick (bearish rejection)
        upper_wick = current_high - max(current_open, current_close)
        is_large_upper_wick = upper_wick / bar_range >= self.wick_ratio_threshold

        # Check for large lower wick (bullish rejection)
        lower_wick = min(current_open, current_close) - current_low
        is_large_lower_wick = lower_wick / bar_range >= self.wick_ratio_threshold

        signal_direction = None
        reason = ""
        entry_price = current_close
        
        # Check confirmation for bearish rejection (large upper wick)
        if is_large_upper_wick and ctx.bar.close < ctx.bar.open: # Ensure current bar is bearish
            if len(closes) > self.confirmation_bars:
                # Next bar (current bar) confirms by closing lower than the rejection bar's close
                if current_close < closes[-2]: # closes[-1] is current bar, closes[-2] is prev bar
                    signal_direction = "short"
                    reason = "bearish_rejection_upper_wick_confirmed"
                    entry_price = current_close

        # Check confirmation for bullish rejection (large lower wick)
        if is_large_lower_wick and ctx.bar.close > ctx.bar.open: # Ensure current bar is bullish
            if len(closes) > self.confirmation_bars:
                # Next bar (current bar) confirms by closing higher than the rejection bar's close
                if current_close > closes[-2]:
                    signal_direction = "long"
                    reason = "bullish_rejection_lower_wick_confirmed"
                    entry_price = current_close

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            
            # Apply regime filters for confidence scaling
            confidence = 0.6
            fear_greed = ctx.features.get("fear_greed_index", 50)
            funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

            # Boost confidence in favorable regimes
            if signal_direction == "long":
                if fear_greed < 40: # Fear regime
                    confidence += 0.1
                if funding_rate < 0: # Negative funding rate (short squeeze potential)
                    confidence += 0.1
            elif signal_direction == "short":
                if fear_greed > 60: # Greed regime
                    confidence += 0.1
                if funding_rate > 0: # Positive funding rate (long squeeze potential)
                    confidence += 0.1
            
            confidence = min(1.0, confidence) # Cap confidence at 1.0

            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "bar_range": bar_range,
                    "upper_wick_ratio": upper_wick / bar_range if bar_range > 0 else 0,
                    "lower_wick_ratio": lower_wick / bar_range if bar_range > 0 else 0,
                    "current_volume": current_bar_volume,
                    "avg_volume_20": avg_volume,
                    "fear_greed_index": fear_greed,
                    "funding_rate_solusdt": funding_rate,
                    "entry_price": entry_price,
                }
            )
        
        # If we have a position, consider exit conditions
        if ctx.has_position():
            # No specific exit logic beyond declared SL/TP for now,
            # but this is where you'd add partial wick retracement or structure stop
            # For simplicity, we rely on the declared SL/TP for this version.
            # If a more complex exit is needed, it would look like:
            # if ctx.position_direction() == "long" and current_close < some_level:
            #     return ctx.signal("flat", confidence=0.8, metadata={"reason": "partial_wick_retracement_exit"})
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset adaptive state
        pass