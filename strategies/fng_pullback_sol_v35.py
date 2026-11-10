from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.dip_threshold = 0.035  # 3.5% pullback / rally required
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

        if len(closes) < self.lookback:
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        rsi_14 = self._rsi(closes, 14)

        if mean_24 is None or rsi_14 is None:
            return None

        # Check open positions for exit condition (sentiment normalization or mean reversion)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if current_close >= mean_24 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_or_sentiment_normalized",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "rsi": rsi_14,
                        },
                    )
            elif direction == "short":
                if current_close <= mean_24 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_or_sentiment_normalized",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "rsi": rsi_14,
                        },
                    )
            return None

        # Lookback extremes over prior 48 bars
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Avoid zero division
        if recent_high <= 0 or recent_low <= 0:
            return None

        pullback_from_high = (recent_high - current_close) / recent_high
        rally_from_low = (current_close - recent_low) / recent_low

        # Regime filter: avoid taking counter-trend longs in meltdown/crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: Fear sentiment + distinct price dip + oversold RSI confirm
        if sentiment <= -0.30 and pullback_from_high >= self.dip_threshold and rsi_14 < 38.0:
            conf = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_dip_oversold_entry",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rsi": rsi_14,
                    "recent_high": recent_high,
                },
            )

        # Short Setup: Euphoric sentiment + distinct rally + overbought RSI confirm
        if sentiment >= 0.30 and rally_from_low >= self.dip_threshold and rsi_14 > 62.0:
            conf = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_overbought_entry",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rsi": rsi_14,
                    "recent_low": recent_low,
                },
            )

        return None