from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "Sentiment Conditioned Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 24
        self.rsi_period = 14
        self.cooldown_bars = 14
        self.last_trade_bar = -999
        self.min_sentiment = 28.0
        self.max_sentiment = 72.0
        self.min_slope_pct = 0.0012

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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
        closes = ctx.closes(55)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme crisis regimes
        if ctx.regime == "crisis":
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        current_close = closes[-1]
        prev_close = closes[-2]

        ema_current = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        ema_lag = self._ema(closes[:-4], self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_current is None or ema_prev is None or ema_lag is None or rsi is None:
            return None

        ema_slope_pct = (ema_current - ema_lag) / ema_lag
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position exit management (less noisy exits to let trades run)
        if has_pos:
            if pos_dir == "long":
                if (current_close < ema_current * 0.995) or (rsi > 80.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_trend_breakdown_or_rsi_extreme",
                            "rsi": round(rsi, 2),
                            "ema24": round(ema_current, 2),
                            "price": round(current_close, 2),
                            "fg_index": fg_index,
                        },
                    )
            elif pos_dir == "short":
                if (current_close > ema_current * 1.005) or (rsi < 20.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_trend_breakdown_or_rsi_extreme",
                            "rsi": round(rsi, 2),
                            "ema24": round(ema_current, 2),
                            "price": round(current_close, 2),
                            "fg_index": fg_index,
                        },
                    )
            return None

        # Check mandatory cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: strictly trade in the stable 28-72 band
        if not (self.min_sentiment <= fg_index <= self.max_sentiment):
            return None

        # Long Entry: Requires clear bullish cross or fresh bounce over strongly rising EMA
        is_bull_cross = prev_close <= ema_prev and current_close > ema_current
        is_strong_slope_bull = ema_slope_pct > self.min_slope_pct
        rsi_bull_sweetspot = 52.0 <= rsi <= 66.0

        if is_bull_cross and is_strong_slope_bull and rsi_bull_sweetspot:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (rsi - 50.0) / 80.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_bull_crossover",
                    "rsi": round(rsi, 2),
                    "ema24": round(ema_current, 2),
                    "slope_pct": round(ema_slope_pct, 5),
                    "fg_index": fg_index,
                    "price": round(current_close, 2),
                },
            )

        # Short Entry: Requires clear bearish cross with strongly falling EMA
        is_bear_cross = prev_close >= ema_prev and current_close < ema_current
        is_strong_slope_bear = ema_slope_pct < -self.min_slope_pct
        rsi_bear_sweetspot = 34.0 <= rsi <= 48.0

        if is_bear_cross and is_strong_slope_bear and rsi_bear_sweetspot:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (50.0 - rsi) / 80.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_bear_crossover",
                    "rsi": round(rsi, 2),
                    "ema24": round(ema_current, 2),
                    "slope_pct": round(ema_slope_pct, 5),
                    "fg_index": fg_index,
                    "price": round(current_close, 2),
                },
            )

        return None