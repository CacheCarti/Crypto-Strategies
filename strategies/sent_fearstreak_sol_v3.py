from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakSOL(Strategy):
    METADATA = {
        "name": "SentimentFearStreakSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_streak_threshold = 35 # F&G index below this is considered "fearful"
        self.min_fear_duration = 3 # Number of consecutive daily bars where F&G is below threshold
        self.stabilization_lookback = 5 # Bars to check for stabilization after fear streak
        self.cooldown_bars = 12 # Cooldown period after a trade (12 hours for 1h bars)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # For stabilization detection
        self.stabilization_ema_period = 10
        self.stabilization_std_period = 10
        self.min_stabilization_volatility_reduction_pct = 0.25 # 25% reduction in volatility

        # State to track fear streak
        self.fear_streak_count = 0
        self.in_fear_streak = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.stabilization_std_period * 2) # Need enough for stddev comparison
        highs = ctx.highs(self.stabilization_std_period * 2)
        lows = ctx.lows(self.stabilization_std_period * 2)
        volumes = ctx.volumes(self.stabilization_std_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]: # Ensure enough data for all indicators
            return None

        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        current_close = ctx.bar.close

        # Daily cadence for Fear & Greed index. Only update fear streak count once per day.
        # Simple heuristic: if the hour is 0 (midnight UTC), it's a new day's reading.
        # This assumes F&G index updates daily and we process it once a day effectively.
        if ctx.bar.timestamp.hour == 0:
            if current_fear_greed <= self.fear_streak_threshold:
                self.fear_streak_count += 1
            else:
                self.fear_streak_count = 0
            self.in_fear_streak = (self.fear_streak_count >= self.min_fear_duration)

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic
        if not ctx.has_position() and self.in_fear_streak:
            # Look for stabilization
            # Price must be above a short-term EMA to signal stabilization/recovery
            short_ema = self._ema(closes, self.stabilization_ema_period)
            if short_ema is None or current_close < short_ema:
                return None

            # Check for reduced volatility as a sign of stabilization
            # Compare current volatility with volatility during the fear streak
            # We need enough history for two stddev calculations
            if len(closes) < self.stabilization_std_period * 2:
                return None

            # Volatility of the recent past (stabilization window)
            recent_volatility = self._stddev(closes[-self.stabilization_std_period:], self.stabilization_std_period)
            # Volatility of the period just before stabilization (during fear)
            # This is a proxy, ideally we'd track volatility during the exact fear streak
            # For simplicity, we take the period before the recent stabilization window
            prior_volatility = self._stddev(closes[-(self.stabilization_std_period * 2):-self.stabilization_std_period], self.stabilization_std_period)

            if recent_volatility is None or prior_volatility is None or prior_volatility == 0:
                return None

            volatility_reduction = (prior_volatility - recent_volatility) / prior_volatility

            # Ensure volatility has indeed reduced AND it's not a tiny wobble
            if volatility_reduction > self.min_stabilization_volatility_reduction_pct and recent_volatility > 0.001 * current_close: # Avoid division by zero or near-zero vol
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_streak_stabilization_long",
                        "fear_greed_index": current_fear_greed,
                        "fear_streak_duration": self.fear_streak_count,
                        "current_price": current_close,
                        "short_ema": short_ema,
                        "recent_volatility": recent_volatility,
                        "prior_volatility": prior_volatility,
                        "volatility_reduction_pct": volatility_reduction,
                    }
                )

        # Exit logic (handled by SL/TP, but can add time-based or condition-based exits)
        # For this strategy, we rely on the declared SL/TP and horizon
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset state or log if needed.
        # We update last_trade_bar on signal, so no need to update here for cooldown.
        pass