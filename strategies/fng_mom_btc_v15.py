from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slope_lookback = 3
        self.rsi_period = 14
        self.last_exit_bar = -999
        self.cooldown_bars = 6

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after any position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = float(ctx.features.get("fear_greed_index", 50.0))
        # Filter: avoid crowded euphoric tops and panic liquidations
        sentiment_ok = 25.0 <= fng <= 75.0

        ema_now = self._ema(closes, self.ema_fast_period)
        ema_prev = self._ema(closes[:-self.ema_slope_lookback], self.ema_fast_period)
        if ema_now is None or ema_prev is None:
            return None

        ema_slope = (ema_now - ema_prev) / ema_prev
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        price = ctx.bar.close
        price_prev = closes[-2]

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit when momentum breaks below EMA or extreme overbought
                if price < ema_now or rsi > 78.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "price": round(price, 2),
                            "ema": round(ema_now, 2),
                            "rsi": round(rsi, 2),
                        },
                    )
            elif direction == "short":
                # Exit when momentum breaks above EMA or extreme oversold
                if price > ema_now or rsi < 22.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_momentum_exhaustion",
                            "price": round(price, 2),
                            "ema": round(ema_now, 2),
                            "rsi": round(rsi, 2),
                        },
                    )
            return None

        if not sentiment_ok:
            return None

        # Cross / trigger condition to prevent firing every bar
        long_cross = (price_prev <= ema_prev) and (price > ema_now)
        short_cross = (price_prev >= ema_prev) and (price < ema_now)

        # Long Entry: Price cross above rising EMA, confirmed by healthy RSI range
        if long_cross and ema_slope > 0.0005 and 45.0 < rsi < 68.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ema_cross_sentiment_filtered",
                    "price": round(price, 2),
                    "ema": round(ema_now, 2),
                    "ema_slope": round(ema_slope, 5),
                    "fng": round(fng, 1),
                    "rsi": round(rsi, 2),
                },
            )

        # Short Entry: Price cross below falling EMA, confirmed by healthy RSI range
        if short_cross and ema_slope < -0.0005 and 32.0 < rsi < 55.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ema_cross_sentiment_filtered",
                    "price": round(price, 2),
                    "ema": round(ema_now, 2),
                    "ema_slope": round(ema_slope, 5),
                    "fng": round(fng, 1),
                    "rsi": round(rsi, 2),
                },
            )

        return None