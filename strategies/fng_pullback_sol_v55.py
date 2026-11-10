from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SolSentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035       # 3.5% deep pullback threshold
        self.cooldown_bars = 12         # Mandatory post-exit & post-trade cooldown
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Slow sentiment regime filter (-1.0 to 1.0)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Skip during severe market breakdown / crisis panic
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        mean_price = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)
        if mean_price is None or rsi is None:
            return None

        curr_close = ctx.bar.close

        # Position Management: Exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion profit-taking or sentiment neutralization exit
            if pos_dir == "long":
                if curr_close >= mean_price and rsi >= 50.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "mean_reversion_long_exit",
                        "close": curr_close,
                        "mean": mean_price,
                        "rsi": rsi,
                        "sentiment": sentiment
                    })
                if sentiment > 0.1:  # Sentiment normalized away from fear
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "sentiment_normalized_long_exit",
                        "sentiment": sentiment,
                        "close": curr_close
                    })
            elif pos_dir == "short":
                if curr_close <= mean_price and rsi <= 50.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "mean_reversion_short_exit",
                        "close": curr_close,
                        "mean": mean_price,
                        "rsi": rsi,
                        "sentiment": sentiment
                    })
                if sentiment < -0.1:  # Sentiment normalized away from euphoria
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "sentiment_normalized_short_exit",
                        "sentiment": sentiment,
                        "close": curr_close
                    })
            return None

        # Mandatory Cooldown Gate to prevent overtrading and fee drag
        if (ctx.bar_index - self.last_exit_bar < self.cooldown_bars) or \
           (ctx.bar_index - self.last_entry_bar < self.cooldown_bars):
            return None

        # Price triggers: Pullback from recent extremes
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        pullback_from_high = (recent_high - curr_close) / recent_high
        rally_from_low = (curr_close - recent_low) / recent_low

        # High-conviction selective entries
        # Long Setup: Fearful sentiment + deep pullback from 48-bar high + short-term oversold confirmation
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct and rsi < 38.0:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(sentiment) * 0.25)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_long",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rsi": rsi,
                    "recent_high": recent_high,
                    "close": curr_close
                }
            )

        # Short Setup: Euphoric sentiment + extended rally from 48-bar low + short-term overbought confirmation
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct and rsi > 62.0:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(sentiment) * 0.25)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_rally_short",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rsi": rsi,
                    "recent_low": recent_low,
                    "close": curr_close
                }
            )

        return None