from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullbackCrossConfirm(Strategy):
    METADATA = {
        "name": "SolSentimentPullbackCrossConfirm",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.last_exit_bar = -100
        self.cooldown_bars = 6
        self.bars_in_pos = 0

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        mean_price = self._sma(closes, 24)
        if mean_price is None:
            return None

        # Manage open position
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Mean reversion or sentiment normalization exit
            if direction == "long":
                if current_close >= mean_price or sentiment >= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma24": mean_price,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_pos,
                        },
                    )
            elif direction == "short":
                if current_close <= mean_price or sentiment <= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma24": mean_price,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_pos,
                        },
                    )
            return None

        # Cooldown guard after exit to control frequency and overcome friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        pullback_from_high = (rolling_high - current_close) / rolling_high
        rally_from_low = (current_close - rolling_low) / rolling_low

        # Filter out extreme crisis regime to protect capital
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long: Sentiment fear + >= 3% dip from 48h high + cross-asset confirmation (BTC stabilizing/positive)
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct:
            if btc_ret >= -0.5:
                confidence = min(1.0, 0.65 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "fear_pullback_cross_confirmed",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "btc_return_pct": btc_ret,
                        "rolling_high": rolling_high,
                        "close": current_close,
                    },
                )

        # Short: Sentiment euphoria + >= 3% rally from 48h low + cross-asset confirmation (BTC decelerating)
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct:
            if btc_ret <= 0.5:
                confidence = min(1.0, 0.65 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "euphoria_rally_cross_confirmed",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "btc_return_pct": btc_ret,
                        "rolling_low": rolling_low,
                        "close": current_close,
                    },
                )

        return None