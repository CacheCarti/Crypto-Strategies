from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.max_hold_bars = 24

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
        closes = ctx.closes(60)
        highs = ctx.highs(60)
        lows = ctx.lows(60)

        if len(closes) < 55:
            return None

        fng = ctx.features.get("fear_greed_index", 50)
        if fng is None:
            fng = 50.0

        ema20_curr = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20_curr is None or ema20_prev is None or ema50 is None:
            return None

        current_price = ctx.bar.close
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Sentiment extreme protection: exit if sentiment overheats or crashes violently
            if fng < 20 or fng > 80:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "sentiment_extreme_reached",
                    "fng": fng,
                    "price": current_price
                })

            # Trend breakdown exit
            if pos_dir == "long" and current_price < ema20_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_trend_broken",
                    "ema20": round(ema20_curr, 2),
                    "price": current_price
                })
            elif pos_dir == "short" and current_price > ema20_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_trend_broken",
                    "ema20": round(ema20_curr, 2),
                    "price": current_price
                })
            return None

        # Entry logic: must respect cooldown
        if bars_since_exit < self.cooldown_bars:
            return None

        # Filter out extreme sentiment regimes
        if fng < 25 or fng > 75:
            return None

        # Ensure market is not in full panic meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev

        # Long trigger: Fresh crossover above EMA20 with upward slope, confirmed by EMA50 base
        prev_price = closes[-2]
        long_cross = (prev_price <= ema20_prev) and (current_price > ema20_curr)
        if long_cross and ema20_slope > 0.0005 and current_price > ema50:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_neutral_bullish_crossover",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50, 2),
                    "slope": round(ema20_slope * 10000, 2),
                    "price": current_price
                }
            )

        # Short trigger: Fresh crossover below EMA20 with downward slope, confirmed by EMA50 resistance
        short_cross = (prev_price >= ema20_prev) and (current_price < ema20_curr)
        if short_cross and ema20_slope < -0.0005 and current_price < ema50:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_neutral_bearish_crossover",
                    "fng": fng,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50, 2),
                    "slope": round(ema20_slope * 10000, 2),
                    "price": current_price
                }
            )

        return None