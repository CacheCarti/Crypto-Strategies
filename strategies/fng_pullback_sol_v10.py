from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullbackEvent(Strategy):
    METADATA = {
        "name": "SolSentimentPullbackEvent",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.sentiment_extreme = 0.25

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Long Exit: Mean reversion target reached or sentiment fully normalized to neutral/bullish
            if pos_dir == "long":
                if current_close >= mean_24 or sentiment >= 0.05:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mean_reverted_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "price": current_close,
                        "sma24": mean_24
                    })

            # Short Exit: Mean reversion target reached or sentiment normalized
            elif pos_dir == "short":
                if current_close <= mean_24 or sentiment <= -0.05:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mean_reverted_or_sentiment_normalized",
                        "sentiment": sentiment,
                        "price": current_close,
                        "sma24": mean_24
                    })
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Stricter Event Boundary: Discrete breakout/crossing of the 3% pullback line
        # Use previous 48 bars (excluding current bar) to establish reference peak/trough
        ref_high = max(highs[-self.lookback - 1:-1])
        ref_low = min(lows[-self.lookback - 1:-1])

        pullback_long_threshold = ref_high * (1.0 - self.pullback_pct)
        rally_short_threshold = ref_low * (1.0 + self.pullback_pct)

        prev_close = closes[-2]

        # Long Trigger: Fear extreme + price fresh drop through 3% pullback boundary (event crossing)
        if sentiment < -self.sentiment_extreme:
            if prev_close >= pullback_long_threshold and current_close < pullback_long_threshold:
                conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
                return ctx.signal("long", confidence=conf, metadata={
                    "reason": "fear_sentiment_dip_breakout_cross",
                    "sentiment": sentiment,
                    "ref_high": ref_high,
                    "threshold": pullback_long_threshold,
                    "close": current_close,
                    "pullback_depth": (ref_high - current_close) / ref_high
                })

        # Short Trigger: Euphoria extreme + price fresh rally through 3% rally boundary
        if sentiment > self.sentiment_extreme:
            if prev_close <= rally_short_threshold and current_close > rally_short_threshold:
                conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
                return ctx.signal("short", confidence=conf, metadata={
                    "reason": "euphoria_rally_spike_cross",
                    "sentiment": sentiment,
                    "ref_low": ref_low,
                    "threshold": rally_short_threshold,
                    "close": current_close,
                    "rally_height": (current_close - ref_low) / ref_low
                })

        return None