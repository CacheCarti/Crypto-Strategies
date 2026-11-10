from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.atr_period = 14
        self.last_exit_bar = -100
        self.cooldown_bars = 6
        self.prev_ema = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate current and prior EMA20 to detect slope
        current_ema = self._ema(closes, self.ema_period)
        prev_closes = closes[:-1]
        prior_ema = self._ema(prev_closes, self.ema_period)

        if current_ema is None or prior_ema is None:
            return None

        ema_slope = current_ema - prior_ema
        price = ctx.bar.close

        # Volatility metric for adaptive SL/TP (structure-based risk plan)
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)
        atr = self._atr(highs, lows, closes[-(self.atr_period + 2):], self.atr_period)
        atr_bps = (atr / price) * 10000.0 if atr else 150.0

        # Sentiment gating: avoid extreme reversal zones
        fng = ctx.features.get("fear_greed_index", 50.0)
        sentiment_ok = 25.0 <= fng <= 75.0

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic trend-invalidation exit
            if pos_dir == "long" and (price < current_ema or ema_slope < 0):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_trend_invalidation", "price": price, "ema20": current_ema, "fng": fng}
                )
            elif pos_dir == "short" and (price > current_ema or ema_slope > 0):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_trend_invalidation", "price": price, "ema20": current_ema, "fng": fng}
                )
            return None

        # Mandatory cooldown to prevent overtrading and fee drain
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if not sentiment_ok:
            return None

        # Structure-based volatility stops to avoid allocation trailing stop trap
        sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
        tp_bps = sl_bps * 2.0

        # Entry logic: Fresh slope confirmation + price position
        # Using a slope threshold relative to price to ensure conviction
        slope_pct = (ema_slope / price) * 10000.0

        if price > current_ema and slope_pct > 2.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_conditioned_bullish_momentum",
                    "price": price,
                    "ema20": current_ema,
                    "slope_bps": slope_pct,
                    "fng": fng,
                    "atr_bps": atr_bps
                }
            )

        if price < current_ema and slope_pct < -2.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_conditioned_bearish_momentum",
                    "price": price,
                    "ema20": current_ema,
                    "slope_bps": slope_pct,
                    "fng": fng,
                    "atr_bps": atr_bps
                }
            )

        return None