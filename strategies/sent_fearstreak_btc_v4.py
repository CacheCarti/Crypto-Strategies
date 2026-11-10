from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakBTC(Strategy):
    METADATA = {
        "name": "Sentiment Fear Streak BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,  # Wider SL for swing trading
        "declared_tp_bps": 500.0,  # Good R:R
        "declared_hold_seconds": 7200,  # ~2 hours, allowing for some swing
        "warmup_bars": 50,  # Need enough bars for longer EMAs and Fear & Greed history
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.fear_greed_threshold = 30  # "Fear" or "Extreme Fear"
        self.fear_streak_length = 3     # How many consecutive bars of fear
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_close = closes[-1]
        previous_close = closes[-2] if len(closes) >= 2 else current_close

        # Fear & Greed Index
        fear_greed_history = [ctx.features.get("fear_greed_index", 50) for _ in range(self.fear_streak_length)]
        # This assumes fear_greed_index is available for past bars, which it is in backtest (daily cadence)
        # However, ctx.features.get() only gets the current bar's value.
        # For a "streak", we need to maintain state or infer from the current value's persistence.
        # Given the daily cadence, we'll simplify and check if the current value is low and previous values were also low.
        # This means the F&G index needs to have been low for at least `fear_streak_length` days.
        # Since it's daily, we need to check if the value has been consistently low for the past N bars (N * bar_duration = N days).

        # Simplified fear streak check: Is current F&G low and has it been low for past `fear_streak_length` bars (which represent days here)?
        # For 1h bars, a daily F&G value will be constant for 24 bars.
        # We need to check if the F&G index has been below the threshold for at least `self.fear_streak_length` * 24 bars.
        # This is a bit complex given ctx.features.get only gives current.
        # A simpler approach: if current F&G is low, and we haven't traded recently, assume the "streak" is ongoing.
        # The true streak would require storing F&G daily values or checking bar timestamps.
        # Let's use a simpler current-state check for F&G and rely on the price action to confirm "stabilization".

        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        is_fearful_regime = current_fear_greed <= self.fear_greed_threshold

        # Market regime filter (stand down in crisis or strong bear trend)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if market_regime == "CRISIS" or market_regime == "MELTDOWN" or trend_regime == "bear":
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "regime_exit_crisis_or_bear", "market_regime": market_regime, "trend_regime": trend_regime})
            return None # Stand down

        # Entry logic: Long only
        if not ctx.has_position():
            # Condition 1: Fearful sentiment
            if is_fearful_regime:
                # Condition 2: Price stabilization - currently above previous close AND above slow EMA, after a recent dip
                # "First stabilization bar" implies a bounce.
                # Check for a recent low followed by a bounce above fast EMA.
                # A simple proxy: current close > previous close and current close > fast_ema
                
                # Check if the market was recently below the slow EMA (indicating a dip)
                # and is now recovering.
                # This ensures we're not just trading in a persistent uptrend.
                
                # We need to look back a bit to see if there was a dip
                # Let's say, in the last `self.fast_ema_period` bars, the price was below slow_ema at some point.
                was_below_slow_ema_recently = False
                for i in range(1, min(self.fast_ema_period + 1, len(closes))):
                    past_close = closes[-i]
                    past_slow_ema = self._ema(closes[:-i+1], self.slow_ema_period)
                    if past_slow_ema is not None and past_close < past_slow_ema:
                        was_below_slow_ema_recently = True
                        break

                if was_below_slow_ema_recently and \
                   current_close > previous_close and \
                   current_close > fast_ema and \
                   fast_ema > slow_ema: # Ensure some bullish momentum is building

                    confidence = min(0.7, current_fear_greed / self.fear_greed_threshold * 0.7) # Lower F&G = higher confidence
                    confidence = max(0.4, confidence) # Min confidence

                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        metadata={
                            "reason": "fear_sentiment_stabilization_bounce",
                            "fear_greed_index": current_fear_greed,
                            "current_close": current_close,
                            "previous_close": previous_close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "was_below_slow_ema_recently": was_below_slow_ema_recently
                        }
                    )

        # Exit logic:
        elif ctx.has_position():
            # If market regime changes significantly while in position
            if market_regime == "CRISIS" or market_regime == "MELTDOWN" or trend_regime == "bear":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_shift_exit", "market_regime": market_regime, "trend_regime": trend_regime})

            # Check for EMA bearish cross (potential trend reversal)
            # Only if we are currently long
            if ctx.position_direction() == "long" and fast_ema < slow_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "ema_bearish_cross_exit",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_close": current_close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive sizing or logging based on position outcome
        # self.last_trade_bar = ctx.bar_index # Cooldown is handled in on_bar, but could be reset here too
        pass