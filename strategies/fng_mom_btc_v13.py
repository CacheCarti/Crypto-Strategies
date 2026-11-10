from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.slope_lookback = 3
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema_series(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        emas = [ema]
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
            emas.append(ema)
        return emas

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
        closes = ctx.closes(45)
        highs = ctx.highs(45)
        lows = ctx.lows(45)

        if len(closes) < 35:
            return None

        current_price = ctx.bar.close
        fng = ctx.features.get("fear_greed_index", 50.0)

        # In-trade management: dynamic exit and time stop
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_pos = ctx.bar_index - self.entry_bar

            # Timed expiration
            if bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_exit", "bars_held": bars_in_pos, "price": current_price}
                )

            # Sentiment extreme protection
            if fng > 82 or fng < 18:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "sentiment_extreme_invalidation", "fng": fng, "price": current_price}
                )

            # Trend breakdown exit
            emas = self._ema_series(closes, self.ema_period)
            if emas is not None:
                current_ema = emas[-1]
                if pos_dir == "long" and current_price < current_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "trend_breakdown_long", "ema20": current_ema, "price": current_price}
                    )
                elif pos_dir == "short" and current_price > current_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "trend_breakdown_short", "ema20": current_ema, "price": current_price}
                    )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: strictly between 25 and 75
        if fng <= 25.0 or fng >= 75.0:
            return None

        emas = self._ema_series(closes, self.ema_period)
        if emas is None or len(emas) <= self.slope_lookback:
            return None

        curr_ema = emas[-1]
        prev_ema = emas[-1 - self.slope_lookback]
        ema_slope = (curr_ema - prev_ema) / prev_ema

        # Volatility check via ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        atr_pct = atr / current_price
        # Dynamic volatility stop calculation (in basis points)
        dyn_sl_bps = max(150.0, min(350.0, (atr / current_price) * 1.6 * 10000))
        dyn_tp_bps = dyn_sl_bps * 2.1

        # Entry triggers: Trend alignment with EMA slope confirmation
        # Minimum slope threshold ensures distinct directional momentum
        min_slope = 0.0008

        # Long entry: price > EMA20, EMA20 rising, previous close just crossed or held above
        if current_price > curr_ema and ema_slope > min_slope and closes[-2] > emas[-2]:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(ema_slope) * 200)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                metadata={
                    "reason": "bullish_momentum_fng_filtered",
                    "fng": fng,
                    "ema20": curr_ema,
                    "ema_slope": ema_slope,
                    "atr_pct": atr_pct,
                    "price": current_price
                }
            )

        # Short entry: price < EMA20, EMA20 falling, previous close held below
        if current_price < curr_ema and ema_slope < -min_slope and closes[-2] < emas[-2]:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(ema_slope) * 200)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                metadata={
                    "reason": "bearish_momentum_fng_filtered",
                    "fng": fng,
                    "ema20": curr_ema,
                    "ema_slope": ema_slope,
                    "atr_pct": atr_pct,
                    "price": current_price
                }
            )

        return None