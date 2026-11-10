from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeSessionFadeBTC(Strategy):
    METADATA = {
        "name": "EuropeSessionFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours for fade
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 9   # UTC 9 AM (allow entries for 3 hours, 7, 8, 9)
        self.exit_hour = 12       # UTC 12 PM (exit by this hour)
        self.lookback_period = 20 # For EMA and Std Dev
        self.stretch_factor = 2.0 # How many std devs away from EMA is considered stretched
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Cooldown
        self.cooldown_bars = 5

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for existing positions
        if ctx.has_position():
            if current_hour >= self.exit_hour and ctx.position_direction() != "flat":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_time", "current_hour": current_hour})
            return None # Hold position until exit condition or new signal

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Entry window check (UTC 7-9 AM for entry)
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            return None

        # Market regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Avoid trading in crisis or extreme fear/greed
        if market_regime in ["CRISIS", "MELTDOWN"] or fear_greed < 20 or fear_greed > 80:
            return None

        # Calculate indicators
        ema = self._ema(closes, self.lookback_period)
        std_dev = self._stddev(closes, self.lookback_period)

        if ema is None or std_dev is None or std_dev == 0:
            return None

        current_price = ctx.bar.close
        upper_band = ema + self.stretch_factor * std_dev
        lower_band = ema - self.stretch_factor * std_dev

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Fade logic: if price is stretched above EMA, consider shorting. If stretched below, consider longing.
        # Ensure we are not fading a strong trend (e.g. price consistently above/below EMA)
        # Check for stalling/reversal (current price is at the extreme, but previous bar might have been even more extreme)
        
        # Short signal: Price stretched significantly above EMA and showing signs of stalling
        if current_price > upper_band:
            # Check if current bar's high is not significantly higher than previous high, suggesting stall
            if closes[-2] > lower_band and closes[-2] < upper_band: # Previous bar was not already extremely stretched
                signal_direction = "short"
                reason = "session_fade_upper_band_short"
                confidence = 0.6 + (current_price - upper_band) / (std_dev * 2) # Higher confidence for more stretch

        # Long signal: Price stretched significantly below EMA and showing signs of stalling
        elif current_price < lower_band:
            # Check if current bar's low is not significantly lower than previous low, suggesting stall
            if closes[-2] < upper_band and closes[-2] > lower_band: # Previous bar was not already extremely stretched
                signal_direction = "long"
                reason = "session_fade_lower_band_long"
                confidence = 0.6 + (lower_band - current_price) / (std_dev * 2) # Higher confidence for more stretch

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(0.9, confidence),
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "current_price": current_price,
                    "ema": ema,
                    "std_dev": std_dev,
                    "upper_band": upper_band,
                    "lower_band": lower_band,
                    "fear_greed_index": fear_greed,
                    "market_regime": market_regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log closed position details or adapt strategy parameters
        pass