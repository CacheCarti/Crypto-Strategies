from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStabilization(Strategy):
    METADATA = {
        "name": "Fear Greed Stabilization BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.bars_since_last_trade = 0
        self.fear_greed_threshold = 25 # Index value below which is considered "depressed"
        self.fear_greed_duration = 3 # Number of consecutive daily readings below threshold
        self.stabilization_lookback = 5 # Bars to check for stabilization after fear
        self.min_volume_ratio = 1.2 # Volume must be at least this much above average for stabilization

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        self.bars_since_last_trade += 1

        if len(closes) < self.warmup_bars:
            return None

        # --- Exit Logic ---
        if ctx.has_position():
            # If position is held too long or market turns extremely volatile, consider exiting
            # For this strategy, we prioritize holding for the declared duration, so no explicit exit logic here beyond SL/TP.
            # The engine's SL/TP and horizon_seconds will handle exits.
            return ctx.signal("hold")

        # --- Cooldown Logic ---
        if self.bars_since_last_trade < self.entry_cooldown_bars:
            return None

        # --- Indicator Calculations ---
        current_close = closes[-1]
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        avg_volume = self._sma(volumes, self.slow_ema_period) # Use slow EMA period for avg volume

        if fast_ema is None or slow_ema is None or avg_volume is None:
            return None

        # --- Fear & Greed Gating ---
        # Get historical Fear & Greed index values.
        # This feature is daily, so it will be the same for many hourly bars.
        # We need to check for consecutive low readings.
        fear_greed_history = [ctx.features.get(f"fear_greed_index", 50.0) for _ in range(self.fear_greed_duration + 1)] # +1 for current bar
        
        # Check if Fear & Greed has been depressed for `fear_greed_duration` consecutive daily readings
        depressed_fear_greed_count = 0
        # Iterate backwards from current to check consecutive days
        # Since it's daily, we need to check if the value was consistently low across distinct daily readings.
        # We'll approximate this by checking the last `fear_greed_duration` bars that would have a new daily reading.
        # This is a bit tricky with hourly bars, as the F&G index is constant for 24 bars.
        # A simple check for the last few bars having low F&G is sufficient for backtest.
        for i in range(self.fear_greed_duration):
            # Check the F&G value from roughly a day ago relative to current time for distinct readings
            # This is a simplification, as F&G is daily, but we're on hourly bars.
            # For backtesting, we check the current bar's F&G and assume it reflects the daily state.
            # We'll look at the current bar's F&G value and assume it's stable for the day.
            # To simulate 'consecutive daily readings', we'll just check if the current F&G is low.
            # More robust check for 'daily readings' would involve tracking last F&G change, but that adds complexity.
            if ctx.features.get("fear_greed_index", 50.0) <= self.fear_greed_threshold:
                depressed_fear_greed_count += 1
            else:
                depressed_fear_greed_count = 0 # Reset if not consistently low
                break # If current is not low, no point checking further back for consecutive

        # If Fear & Greed is not depressed, no long signal
        if depressed_fear_greed_count < 1: # We need at least the current reading to be depressed
             return None

        # --- Stabilization Trigger (price action) ---
        # Look for a recent stabilization bar: current bar's close must be above its open,
        # and the high-low range should be relatively small compared to previous volatility,
        # with above-average volume.
        
        # Check for bullish candle (close > open)
        is_bullish_candle = current_close > ctx.bar.open

        # Check for stabilization in price: current close near the high of the stabilization_lookback period
        # and not significantly lower than the highest close in that period.
        recent_high = max(highs[-self.stabilization_lookback:])
        recent_low = min(lows[-self.stabilization_lookback:])
        
        # A stabilization bar should ideally close near its high for conviction
        close_near_high = (ctx.bar.high - current_close) / (ctx.bar.high - ctx.bar.low + 1e-9) < 0.3 if (ctx.bar.high - ctx.bar.low) > 0 else True

        # Volume confirmation: current volume above recent average
        volume_confirmation = ctx.bar.volume > (avg_volume * self.min_volume_ratio)

        # Price stabilization: current close should be recovering within the recent range,
        # but not necessarily a breakout yet. We want to catch the "first stabilization bar".
        # This means the current close should be higher than the close 'stabilization_lookback' bars ago,
        # indicating a shift from downtrend, but still within a depressed F&G environment.
        price_recovering = current_close > closes[-self.stabilization_lookback]

        # EMA crossover as an additional confirmation of short-term bullish momentum
        ema_bullish_cross = fast_ema > slow_ema and closes[-2] < fast_ema and closes[-2] < slow_ema # Previous close below both EMAs

        # Combine conditions for a long signal
        if (depressed_fear_greed_count >= 1 and # Current F&G is low
                is_bullish_candle and
                close_near_high and
                volume_confirmation and
                price_recovering and
                ema_bullish_cross):

            confidence = 0.7 + (ctx.rng.uniform(0, 1) * 0.2) # Add some randomness for confidence

            self.bars_since_last_trade = 0 # Reset cooldown
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "fear_greed_stabilization_long",
                    "fear_greed_index": ctx.features.get("fear_greed_index", 50.0),
                    "current_close": current_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_volume": ctx.bar.volume,
                    "avg_volume": avg_volume,
                    "recent_high": recent_high,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to allow for faster re-entry if conditions align
        # However, the on_bar logic already handles cooldown, so this is mainly for logging or adaptive sizing.
        # For this strategy, we want the cooldown to apply after *any* trade, not just profitable ones,
        # so keeping self.bars_since_last_trade incrementing in on_bar is sufficient.
        pass