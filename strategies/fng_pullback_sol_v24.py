from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.lookback_extremes = 48
        self.pullback_pct = 0.03
        self.mean_period = 24
        self.vol_period = 20

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        mean_price = self._sma(closes, self.mean_period)
        atr = self._atr(highs, lows, closes, 14)
        vol_sma = self._sma(volumes, self.vol_period)

        if mean_price is None or atr is None or vol_sma is None:
            return None

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_price) if ctx.position else current_price

            # Exit on mean reversion or sentiment normalization
            if direction == "long":
                if current_price >= mean_price or sentiment >= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                            "entry_price": entry_price,
                        },
                    )
            elif direction == "short":
                if current_price <= mean_price or sentiment <= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                            "entry_price": entry_price,
                        },
                    )
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Cross-asset filter & market state: avoid entering against severe market stress
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        recent_high = max(highs[-self.lookback_extremes:])
        recent_low = min(lows[-self.lookback_extremes:])
        pullback_from_high = (recent_high - current_price) / recent_high
        rally_from_low = (current_price - recent_low) / recent_low

        # Volume state confirmation: require non-dead market (volume at least 70% of 20-bar avg)
        active_volume = ctx.bar.volume >= (0.7 * vol_sma)

        # Long Setup: Fearful sentiment, at least 3% pullback, BTC not crashing severely, volume active
        if sentiment <= -0.3 and pullback_from_high >= self.pullback_pct and active_volume:
            if btc_ret >= -3.5:
                confidence = min(1.0, 0.60 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=350.0,
                    take_profit_bps=500.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_fear_dip_sol_pullback",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "recent_high": recent_high,
                        "btc_return_pct": btc_ret,
                        "atr": atr,
                    },
                )

        # Short Setup: Euphoric sentiment, at least 3% rally, BTC not exploding upward, volume active
        if sentiment >= 0.3 and rally_from_low >= self.pullback_pct and active_volume:
            if btc_ret <= 3.5:
                confidence = min(1.0, 0.60 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=350.0,
                    take_profit_bps=500.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_euphoria_rally_sol_exhaustion",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "recent_low": recent_low,
                        "btc_return_pct": btc_ret,
                        "atr": atr,
                    },
                )

        return None