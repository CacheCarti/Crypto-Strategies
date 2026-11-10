from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_len = 20
        self.ema_trend_len = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_bars_held = 4

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_trend_len + 5)
        if len(closes) < self.ema_trend_len + 3:
            return None

        # Cooldown guard after trade exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Indicator calculations
        ema20_curr = self._ema(closes, self.ema_fast_len)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_len)
        ema50_curr = self._ema(closes, self.ema_trend_len)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None:
            return None

        price = ctx.bar.close
        price_prev = closes[-2]
        ema20_slope_bps = ((ema20_curr - ema20_prev) / ema20_prev) * 10000.0

        # Sentiment regime gating: filter out extreme exhaustion/panic reversals
        fng = ctx.features.get("fear_greed_index", 50.0)
        is_moderate_sentiment = (25.0 <= fng <= 75.0)

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Trend exhaustion or counter-cross exits
            if direction == "long":
                if price < ema20_curr and price_prev >= ema20_curr:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "exit_long_ema20_cross_under",
                        "price": price,
                        "ema20": ema20_curr,
                        "fng": fng
                    })
                if fng > 82.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "exit_long_extreme_greed",
                        "price": price,
                        "fng": fng
                    })
            elif direction == "short":
                if price > ema20_curr and price_prev <= ema20_curr:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "exit_short_ema20_cross_over",
                        "price": price,
                        "ema20": ema20_curr,
                        "fng": fng
                    })
                if fng < 18.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "exit_short_extreme_fear",
                        "price": price,
                        "fng": fng
                    })
            return None

        # Stand down entirely if sentiment is near violent inflection points
        if not is_moderate_sentiment:
            return None

        # Entry triggers: Trend alignment + fresh momentum break/continuation
        # Long condition: Price crosses above or holds firmly above rising EMA20, supported by macro EMA50
        bull_breakout = (price > ema20_curr) and (price_prev <= ema20_curr or (price - ema20_curr) / ema20_curr > 0.002)
        if bull_breakout and ema20_slope_bps > 1.5 and price > ema50_curr:
            # Scaled confidence based on moderate sentiment sweet spot (45-65)
            confidence = 0.65 + 0.15 * (1.0 - abs(fng - 55.0) / 30.0)
            return ctx.signal(
                "long",
                confidence=min(max(confidence, 0.5), 0.85),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "trend_momentum_long_filtered",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "slope_bps": round(ema20_slope_bps, 2),
                    "fng": fng
                }
            )

        # Short condition: Price crosses below or holds firmly below falling EMA20, below macro EMA50
        bear_breakdown = (price < ema20_curr) and (price_prev >= ema20_curr or (ema20_curr - price) / ema20_curr > 0.002)
        if bear_breakdown and ema20_slope_bps < -1.5 and price < ema50_curr:
            confidence = 0.65 + 0.15 * (1.0 - abs(fng - 45.0) / 30.0)
            return ctx.signal(
                "short",
                confidence=min(max(confidence, 0.5), 0.85),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "trend_momentum_short_filtered",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "slope_bps": round(ema20_slope_bps, 2),
                    "fng": fng
                }
            )

        return None