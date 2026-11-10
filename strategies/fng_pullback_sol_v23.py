from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_pct = 0.035
        self.lookback = 48
        self.mean_period = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
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

        current_close = ctx.bar.close
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        mean_price = self._sma(closes, self.mean_period)
        if mean_price is None:
            return None

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Mean reversion or sentiment normalization exit
            if direction == "long":
                if current_close >= mean_price or sentiment >= 0.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                        }
                    )
            elif direction == "short":
                if current_close <= mean_price or sentiment <= 0.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                        }
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        # Guard against zero-division
        if rolling_high <= 0 or rolling_low <= 0:
            return None

        pullback_from_high = (rolling_high - current_close) / rolling_high
        rally_from_low = (current_close - rolling_low) / rolling_low

        # Long: Deep dip during fear, below reversion target
        if sentiment < -0.25 and pullback_from_high >= self.pullback_pct and current_close < mean_price:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.5)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": round(pullback_from_high * 100, 2),
                    "rolling_high": rolling_high,
                    "mean_price": mean_price,
                    "close": current_close,
                }
            )

        # Short: Rally during euphoria, above reversion target
        if sentiment > 0.25 and rally_from_low >= self.pullback_pct and current_close > mean_price:
            conf = min(0.9, 0.5 + sentiment * 0.5)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "greed_sentiment_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": round(rally_from_low * 100, 2),
                    "rolling_low": rolling_low,
                    "mean_price": mean_price,
                    "close": current_close,
                }
            )

        return None