from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class PostPanicDrift(Strategy):
    METADATA = {
        "name": "Post-Panic Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 100,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drop_period = 24  # Lookback for sharp drop (24 hours)
        self.stabilization_period = 6 # Bars to wait for stabilization after drop
        self.drift_period = 12 # EMA for drift confirmation
        self.cooldown_bars = 48 # Cooldown period after a trade (2 days)
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        self.price_history = collections.deque(maxlen=self.drop_period + self.stabilization_period + self.drift_period)
        self.volume_history = collections.deque(maxlen=self.drop_period + self.stabilization_period + self.drift_period)

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.price_history.append(ctx.bar.close)
        self.volume_history.append(ctx.bar.volume)

        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.drop_period + self.stabilization_period + self.drift_period)
        volumes = ctx.volumes(self.drop_period + self.stabilization_period + self.drift_period)

        if len(closes) < self.drop_period + self.stabilization_period + self.drift_period:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # 1. Detect a sharp multi-bar drop
        # Calculate the percentage drop over the drop_period
        price_at_start_of_drop_period = closes[-(self.drop_period + self.stabilization_period + self.drift_period)]
        current_price_after_drop_period = closes[-(self.stabilization_period + self.drift_period)]

        if price_at_start_of_drop_period == 0: # Avoid division by zero
            return None

        drop_pct = ((current_price_after_drop_period - price_at_start_of_drop_period) / price_at_start_of_drop_period) * 100

        # Require a significant drop, e.g., > 5%
        if drop_pct > -5.0: # We are looking for a negative drop (price decline)
            return None

        # 2. Wait for stabilization
        # Check if the price has been relatively flat for the stabilization_period
        stabilization_closes = closes[-(self.stabilization_period + self.drift_period):-self.drift_period]
        if len(stabilization_closes) < self.stabilization_period:
            return None

        stabilization_high = max(stabilization_closes)
        stabilization_low = min(stabilization_closes)
        stabilization_range_pct = ((stabilization_high - stabilization_low) / stabilization_low) * 100

        # Require a tight range for stabilization, e.g., < 1%
        if stabilization_range_pct > 1.0:
            return None

        # 3. Confirm slower drift and entry
        # Price should be above a short-term EMA after stabilization
        drift_closes = closes[-self.drift_period:]
        if len(drift_closes) < self.drift_period:
            return None
        
        drift_ema = self._ema(drift_closes, self.drift_period // 2) # Use a shorter EMA for drift
        if drift_ema is None:
            return None

        # Check if current close is above the short-term EMA
        # And also check if the price is showing some slight upward drift
        if ctx.bar.close > drift_ema and ctx.bar.close > stabilization_closes[-1]:
            # Additional filter: Fear & Greed Index should not be in 'Extreme Greed'
            fear_greed = ctx.features.get("fear_greed_index", 50)
            eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

            if fear_greed >= 75: # Avoid entering during extreme greed
                return None
            
            # Additional filter: ensure current price is not too far from the stabilization low
            # to prevent buying into a continued sharp drop
            if ctx.bar.close < stabilization_low * 1.02: # Current price is within 2% of the stabilization low
                if not ctx.has_position():
                    # Calculate confidence based on the depth of the initial drop and stability
                    # Deeper drop and tighter stabilization range increase confidence
                    confidence = min(0.5 + abs(drop_pct) * 0.05 + (1.0 - stabilization_range_pct) * 0.1, 1.0)
                    confidence = max(0.6, confidence) # Ensure minimum confidence

                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        metadata={
                            "reason": "post_panic_drift_entry",
                            "initial_drop_pct": f"{drop_pct:.2f}%",
                            "stabilization_range_pct": f"{stabilization_range_pct:.2f}%",
                            "drift_ema": f"{drift_ema:.2f}",
                            "current_price": f"{ctx.bar.close:.2f}",
                            "fear_greed_index": fear_greed,
                            "eth_return_pct": f"{eth_return_pct:.2f}%"
                        }
                    )

        # Exit logic: if in a position, check for profit taking or stop loss
        if ctx.has_position():
            # For this strategy, let the declared SL/TP handle exits primarily.
            # However, we can add a time-based exit or a trend reversal exit.
            current_ema = self._ema(closes, self.drift_period // 2)
            if current_ema is None:
                return None

            # Exit if price drops below the short-term EMA after entry, indicating drift breakdown
            if ctx.bar.close < current_ema * 0.99: # 1% below EMA as a soft exit
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "drift_breakdown_exit",
                        "current_price": f"{ctx.bar.close:.2f}",
                        "current_ema": f"{current_ema:.2f}"
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the bar index of the last trade to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # You could also use this for adaptive sizing or logging performance
        # For instance, if a trade was very profitable, you might slightly increase confidence for the next.
        # Or if it was a loss, you might decrease it or increase cooldown.
        pass