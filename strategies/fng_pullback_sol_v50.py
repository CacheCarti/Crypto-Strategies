from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_period = 8
        self.last_exit_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        curr_price = ctx.bar.close

        # Calculate dynamic 24-bar mean
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit on mean reversion or sentiment normalization
            if pos_dir == "long":
                if curr_price >= mean_24 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reverted_or_sentiment_normalized",
                            "price": curr_price,
                            "mean_24": mean_24,
                            "sentiment": sentiment,
                        },
                    )
            elif pos_dir == "short":
                if curr_price <= mean_24 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reverted_or_sentiment_normalized",
                            "price": curr_price,
                            "mean_24": mean_24,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Discrete event boundary: Lookback reference high/low excluding the current bar
        ref_high = max(highs[-self.lookback - 1 : -1])
        ref_low = min(lows[-self.lookback - 1 : -1])

        # State transition triggers: crossed 3% threshold strictly on current bar
        prev_close = closes[-2]
        long_thresh = ref_high * (1.0 - self.pullback_pct)
        short_thresh = ref_low * (1.0 + self.pullback_pct)

        # Long: Fear regime, previous bar held above dip, current bar crosses down into dip (discrete event)
        if sentiment <= -0.30:
            if prev_close >= long_thresh and curr_price < long_thresh:
                # Require price to still be above lower extreme to avoid trading falling knives during crashes
                if curr_price > ref_low:
                    pullback_dist = (ref_high - curr_price) / ref_high
                    conf = min(0.85, 0.5 + abs(sentiment) * 0.35)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=380.0,
                        take_profit_bps=480.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "fear_sentiment_dip_event",
                            "sentiment": sentiment,
                            "price": curr_price,
                            "ref_high": ref_high,
                            "pullback_pct": pullback_dist,
                            "mean_24": mean_24,
                        },
                    )

        # Short: Euphoric regime, previous bar held below rally, current bar crosses up into rally (discrete event)
        if sentiment >= 0.30:
            if prev_close <= short_thresh and curr_price > short_thresh:
                # Require price to still be below upper extreme to avoid runaway short squeezes
                if curr_price < ref_high:
                    rally_dist = (curr_price - ref_low) / ref_low
                    conf = min(0.85, 0.5 + abs(sentiment) * 0.35)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=380.0,
                        take_profit_bps=480.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "euphoria_sentiment_rally_event",
                            "sentiment": sentiment,
                            "price": curr_price,
                            "ref_low": ref_low,
                            "rally_pct": rally_dist,
                            "mean_24": mean_24,
                        },
                    )

        return None