from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversal(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        btc_ret = float(ctx.features.get("btc_return_pct", 0.0))

        sma20 = self._sma(closes, 20)
        atr14 = self._atr(highs, lows, closes, 14)
        if sma20 is None or atr14 is None:
            return None

        # Cross-asset volatility normalization to avoid low-vol bleed
        norm_vol = atr14 / current_close

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            # Mean-reversion or sentiment normalization exit
            if direction == "long":
                if current_close >= sma20 or sentiment > 0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_tp_or_sentiment_normalized",
                            "price": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_trade,
                        },
                    )
            elif direction == "short":
                if current_close <= sma20 or sentiment < -0.15:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_tp_or_sentiment_normalized",
                            "price": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_trade,
                        },
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Price extremes over lookback window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])

        pullback_pct = (window_high - current_close) / window_high
        rally_pct = (current_close - window_low) / window_low

        # Volatility condition: ensure sufficient market movement to clear friction
        vol_active = norm_vol >= 0.01

        # Long: Sentiment fear + >=3.0% pullback from 48-bar high + BTC cross-asset stability
        if sentiment < -0.3 and pullback_pct >= 0.03 and btc_ret > -3.0 and vol_active:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_btc_confirmed",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_pct,
                    "btc_return_pct": btc_ret,
                    "norm_vol": norm_vol,
                    "price": current_close,
                    "ref_high": window_high,
                },
            )

        # Short: Sentiment euphoria + >=3.0% rally from 48-bar low + BTC cross-asset check
        if sentiment > 0.3 and rally_pct >= 0.03 and btc_ret < 3.0 and vol_active:
            conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_rally_btc_confirmed",
                    "sentiment": sentiment,
                    "rally_pct": rally_pct,
                    "btc_return_pct": btc_ret,
                    "norm_vol": norm_vol,
                    "price": current_close,
                    "ref_low": window_low,
                },
            )

        return None