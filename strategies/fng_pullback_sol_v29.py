from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentAdaptivePullback(Strategy):
    METADATA = {
        "name": "SentimentAdaptivePullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_period = 8
        self.last_trade_bar = -999

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback:
            return None

        highs = ctx.highs(self.lookback)
        lows = ctx.lows(self.lookback)
        curr_price = ctx.bar.close

        highest_h = max(highs)
        lowest_l = min(lows)

        mean_price = self._sma(closes, 30)
        rsi = self._rsi(closes, 14)
        if mean_price is None or rsi is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle Position Exits: Reversion to mean or sentiment normalization
        if has_pos:
            if pos_dir == "long":
                if curr_price >= mean_price or sentiment >= 0.15 or rsi >= 65.0:
                    return ctx.signal("flat", confidence=0.65, metadata={
                        "reason": "long_mean_reversion_target_met",
                        "price": curr_price,
                        "mean_price": mean_price,
                        "sentiment": sentiment,
                        "rsi": rsi
                    })
            elif pos_dir == "short":
                if curr_price <= mean_price or sentiment <= -0.15 or rsi <= 35.0:
                    return ctx.signal("flat", confidence=0.65, metadata={
                        "reason": "short_mean_reversion_target_met",
                        "price": curr_price,
                        "mean_price": mean_price,
                        "sentiment": sentiment,
                        "rsi": rsi
                    })
            return None

        # Mandatory Cooldown check between trades
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Drawdown / Runup calculations
        dd_from_high = (highest_h - curr_price) / highest_h if highest_h > 0 else 0.0
        up_from_low = (curr_price - lowest_l) / lowest_l if lowest_l > 0 else 0.0

        # Long Setup: Fearful regime + pullback + oversold confirmation
        if sentiment <= -0.25 and dd_from_high >= 0.032 and rsi <= 42.0:
            conf = min(0.90, max(0.55, 0.55 + abs(sentiment) * 0.35))
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_dip",
                    "sentiment": sentiment,
                    "dd_from_high": round(dd_from_high, 4),
                    "rsi": round(rsi, 2),
                    "mean_price": round(mean_price, 2),
                    "price": curr_price
                }
            )

        # Short Setup: Euphoric regime + extension + overbought confirmation
        if sentiment >= 0.25 and up_from_low >= 0.032 and rsi >= 58.0:
            conf = min(0.90, max(0.55, 0.55 + sentiment * 0.35))
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_greed_extension_exhaustion",
                    "sentiment": sentiment,
                    "up_from_low": round(up_from_low, 4),
                    "rsi": round(rsi, 2),
                    "mean_price": round(mean_price, 2),
                    "price": curr_price
                }
            )

        return None