from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_len = 20
        self.ema_slow_len = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_bars_held = 4

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        # Indicator calculations
        ema20_now = self._ema(closes, self.ema_fast_len)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_len)
        ema50 = self._ema(closes, self.ema_slow_len)
        atr = self._atr(highs, lows, closes, period=14)

        if ema20_now is None or ema20_prev is None or ema50 is None or atr is None:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2]
        ema20_slope = (ema20_now - ema20_prev) / ema20_prev

        # Regime gating: Fear & Greed filter between 25 and 75
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        regime_ok = (25.0 <= fg_index <= 75.0)

        # Dynamic stop loss and take profit based on ATR
        atr_bps = (atr / current_price) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_bps * 1.8))
        tp_bps = max(350.0, min(700.0, atr_bps * 3.6))

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Momentum breakdown / trend invalidation exits
            if direction == "long" and current_price < ema20_now and ema20_slope < 0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_breakdown", "price": current_price, "ema20": ema20_now, "fg": fg_index}
                )
            if direction == "short" and current_price > ema20_now and ema20_slope > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_breakdown", "price": current_price, "ema20": ema20_now, "fg": fg_index}
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Signals (Requires sentiment sanity check + fresh price cross or confirmation)
        if not regime_ok:
            return None

        # Long Setup: Price crossed above EMA20 or confirmed above EMA50, EMA20 rising
        long_cross = (prev_price <= ema20_prev and current_price > ema20_now)
        long_trend = (current_price > ema20_now > ema50 and ema20_slope > 0.0003)

        if long_cross and long_trend:
            confidence = min(0.85, 0.55 + (ema20_slope * 200.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_conditioned_long_cross",
                    "price": current_price,
                    "ema20": ema20_now,
                    "ema50": ema50,
                    "fg_index": fg_index,
                    "slope": ema20_slope,
                }
            )

        # Short Setup: Price crossed below EMA20 or confirmed below EMA50, EMA20 falling
        short_cross = (prev_price >= ema20_prev and current_price < ema20_now)
        short_trend = (current_price < ema20_now < ema50 and ema20_slope < -0.0003)

        if short_cross and short_trend:
            confidence = min(0.85, 0.55 + (abs(ema20_slope) * 200.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_conditioned_short_cross",
                    "price": current_price,
                    "ema20": ema20_now,
                    "ema50": ema50,
                    "fg_index": fg_index,
                    "slope": ema20_slope,
                }
            )

        return None