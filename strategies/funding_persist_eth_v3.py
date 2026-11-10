from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateFadeStrategy(Strategy):
    METADATA = {
        "name": "Funding Rate Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for longer holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 3  # How many bars funding must be extreme
        self.funding_threshold = 0.00015  # 0.015% per 8h funding rate (extreme)
        self.cooldown_bars = 12  # Cooldown after a trade (12 hours)
        self.last_signal_bar = -self.cooldown_bars

        # For tracking funding rate persistence
        self.extreme_long_count = 0
        self.extreme_short_count = 0
        self.last_funding_direction = None # "long_fade" or "short_fade"

        # Initialize simple moving averages for trend filter
        self.fast_ma_period = 10
        self.slow_ma_period = 30


    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Calculate SMAs for trend filter
        fast_ma = self._sma(closes, self.fast_ma_period)
        slow_ma = self._sma(closes, self.slow_ma_period)

        if fast_ma is None or slow_ma is None:
            return None

        # Update funding persistence counters
        if current_funding_rate > self.funding_threshold:
            self.extreme_long_count += 1
            self.extreme_short_count = 0
        elif current_funding_rate < -self.funding_threshold:
            self.extreme_short_count += 1
            self.extreme_long_count = 0
        else:
            self.extreme_long_count = 0
            self.extreme_short_count = 0

        signal = None
        current_price = ctx.bar.close

        # Long entry: Extreme negative funding for 'funding_period' bars, AND price below slow MA (bearish trend)
        if (not ctx.has_position() and
            self.extreme_short_count >= self.funding_period and
            self.last_funding_direction != "long_fade" and # Prevent re-entry on same condition
            current_price < slow_ma):
            
            signal = ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "funding_rate_fade_long",
                    "funding_rate": current_funding_rate,
                    "extreme_short_count": self.extreme_short_count,
                    "price": current_price,
                    "slow_ma": slow_ma,
                    "fast_ma": fast_ma
                }
            )
            self.last_signal_bar = ctx.bar_index
            self.last_funding_direction = "long_fade"
            self.extreme_short_count = 0 # Reset counter after signal

        # Short entry: Extreme positive funding for 'funding_period' bars, AND price above slow MA (bullish trend)
        elif (not ctx.has_position() and
              self.extreme_long_count >= self.funding_period and
              self.last_funding_direction != "short_fade" and # Prevent re-entry on same condition
              current_price > slow_ma):

            signal = ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "funding_rate_fade_short",
                    "funding_rate": current_funding_rate,
                    "extreme_long_count": self.extreme_long_count,
                    "price": current_price,
                    "slow_ma": slow_ma,
                    "fast_ma": fast_ma
                }
            )
            self.last_signal_bar = ctx.bar_index
            self.last_funding_direction = "short_fade"
            self.extreme_long_count = 0 # Reset counter after signal

        # Exit condition: Funding rate normalizes (goes below threshold)
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            exit_reason = None
            
            if pos_direction == "long" and current_funding_rate > -self.funding_threshold / 2: # Funding less negative or positive
                exit_reason = "funding_normalized_long_exit"
            elif pos_direction == "short" and current_funding_rate < self.funding_threshold / 2: # Funding less positive or negative
                exit_reason = "funding_normalized_short_exit"
            
            # Also exit if the trend reverses against the position
            if pos_direction == "long" and current_price < fast_ma:
                exit_reason = "trend_reversal_long_exit"
            elif pos_direction == "short" and current_price > fast_ma:
                exit_reason = "trend_reversal_short_exit"

            if exit_reason:
                signal = ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "funding_rate": current_funding_rate,
                        "price": current_price,
                        "fast_ma": fast_ma,
                        "slow_ma": slow_ma
                    }
                )
                self.last_signal_bar = ctx.bar_index
                self.last_funding_direction = None # Allow new entries

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state that should be fresh after a position closes
        self.extreme_long_count = 0
        self.extreme_short_count = 0
        self.last_funding_direction = None
        # You could also implement adaptive sizing here based on position["return_bps"]
        # e.g., if position["return_bps"] > 0, increase next trade size confidence
        # if position["return_bps"] < 0, decrease next trade size confidence
        pass