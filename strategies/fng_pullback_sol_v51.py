from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.sma_period = 20
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        close = ctx.bar.close
        sma20 = self._sma(closes, self.sma_period)

        if sma20 is None:
            return None

        # Position management / Exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Long exits: sentiment normalizes or price reaches mean
            if direction == "long":
                if sentiment > -0.05 or close >= sma20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_reset",
                            "close": close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        }
                    )
            # Short exits: sentiment cools down or price drops below mean
            elif direction == "short":
                if sentiment < 0.05 or close <= sma20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_reset",
                            "close": close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        }
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Price triggers: Pullback from recent extreme
        highest_48 = max(highs[-self.lookback:])
        lowest_48 = min(lows[-self.lookback:])

        pullback_from_high = (highest_48 - close) / highest_48
        rally_from_low = (close - lowest_48) / lowest_48

        # Long Setup: Fearful sentiment + 3% dip from 48-bar high + price below SMA20
        if sentiment < -0.30 and pullback_from_high >= self.pullback_pct and close < sma20:
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.6 + abs(sentiment) * 0.3),
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "fear_dip_pullback_entry",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "close": close,
                    "high_48": highest_48,
                    "sma20": sma20,
                }
            )

        # Short Setup: Euphoric sentiment + 3% rally from 48-bar low + price above SMA20
        if sentiment > 0.30 and rally_from_low >= self.pullback_pct and close > sma20:
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.6 + abs(sentiment) * 0.3),
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "greed_rally_fade_entry",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "close": close,
                    "low_48": lowest_48,
                    "sma20": sma20,
                }
            )

        return None