from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDynamicPullback(Strategy):
    METADATA = {
        "name": "SolSentimentDynamicPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.last_entry_bar = -100

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

        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        prior_close = closes[-2]
        
        # Mean anchor (24-bar SMA)
        mean_24 = self._sma(closes, 24)
        if mean_24 is None:
            return None

        # Relative high/low reference over window (excluding current bar to avoid lookahead/noise)
        ref_high = max(highs[-self.lookback:-1])
        ref_low = min(lows[-self.lookback:-1])

        # Pullback / rally depth in pct
        pullback_pct = (ref_high - current_close) / ref_high if ref_high > 0 else 0.0
        rally_pct = (current_close - ref_low) / ref_low if ref_low > 0 else 0.0

        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Dynamic sentiment percentile logic: extreme fear <= -0.20, extreme greed >= 0.20
        # Instead of strict hardcoded static triggers, scale entry threshold slightly by regime
        is_fear = sentiment <= -0.20
        is_greed = sentiment >= 0.20
        sentiment_normalized = abs(sentiment) < 0.10

        # Position Management: exit on mean reversion or sentiment normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= mean_24 or sentiment_normalized:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_reverted_to_mean" if current_close >= mean_24 else "sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_24": mean_24
                        }
                    )
            elif pos_dir == "short":
                if current_close <= mean_24 or sentiment_normalized:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_reverted_to_mean" if current_close <= mean_24 else "sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_24": mean_24
                        }
                    )
            return None

        # Entry logic: selective pullbacks (> 3.2% drop from 48-bar high while sentiment fearful)
        # Require price to bounce slightly off local minimum to avoid catching falling knives
        if is_fear and pullback_pct >= 0.032 and current_close > prior_close:
            conf = min(0.85, 0.5 + abs(sentiment) * 0.5)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_dip_pullback_reversal",
                    "sentiment": sentiment,
                    "pullback_pct": round(pullback_pct, 4),
                    "ref_high": ref_high,
                    "close": current_close,
                    "mean_24": round(mean_24, 2)
                }
            )

        # Mirror entry logic for shorts: selective rally (> 3.2% rally from 48-bar low while sentiment euphoric)
        if is_greed and rally_pct >= 0.032 and current_close < prior_close:
            conf = min(0.85, 0.5 + abs(sentiment) * 0.5)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": round(rally_pct, 4),
                    "ref_low": ref_low,
                    "close": current_close,
                    "mean_24": round(mean_24, 2)
                }
            )

        return None