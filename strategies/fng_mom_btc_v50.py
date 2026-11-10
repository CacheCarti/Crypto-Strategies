from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentMomentumFilter(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 40
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.bars_in_pos = 0

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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < 45:
            return None

        # Position tracking & explicit exits
        if ctx.has_position():
            self.bars_in_pos += 1
            pos_dir = ctx.position_direction()
            
            # Structural trend break exit
            ema20 = self._ema(closes, self.fast_period)
            if ema20 is not None:
                if pos_dir == "long" and closes[-1] < ema20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_below_ema20", "close": closes[-1], "ema20": round(ema20, 2)}
                    )
                elif pos_dir == "short" and closes[-1] > ema20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_above_ema20", "close": closes[-1], "ema20": round(ema20, 2)}
                    )

            # Max hold time safeguard (~12 hours on 1h bars)
            if self.bars_in_pos >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_exit", "bars_held": self.bars_in_pos}
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Feature: Fear & Greed Index
        fg_index = ctx.features.get("fear_greed_index", 50)
        if not (25 <= fg_index <= 75):
            return None

        # Volatility & Trend indicators
        ema20_curr = self._ema(closes, self.fast_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema40_curr = self._ema(closes, self.slow_period)
        atr14 = self._atr(highs, lows, closes, period=14)

        if ema20_curr is None or ema20_prev is None or ema40_curr is None or atr14 is None:
            return None

        price = closes[-1]
        prev_price = closes[-2]
        volatility_bps = (atr14 / price) * 10000.0

        # Avoid entries in dead markets or hyper-chaotic regimes
        if volatility_bps < 30.0 or volatility_bps > 250.0:
            return None

        ema20_slope = (ema20_curr - ema20_prev) / ema20_prev * 10000.0

        # Long trigger: Fresh break above EMA20 with positive slope & above baseline trend
        bullish_trigger = (
            prev_price <= ema20_prev and
            price > ema20_curr and
            ema20_slope > 1.5 and
            price > ema40_curr
        )

        # Short trigger: Fresh break below EMA20 with negative slope & below baseline trend
        bearish_trigger = (
            prev_price >= ema20_prev and
            price < ema20_curr and
            ema20_slope < -1.5 and
            price < ema40_curr
        )

        if bullish_trigger:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_ema_cross_sentiment_filtered",
                    "price": price,
                    "ema20": round(ema20_curr, 2),
                    "ema40": round(ema40_curr, 2),
                    "ema20_slope_bps": round(ema20_slope, 2),
                    "fg_index": fg_index,
                    "vol_bps": round(volatility_bps, 2)
                }
            )

        if bearish_trigger:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_ema_cross_sentiment_filtered",
                    "price": price,
                    "ema20": round(ema20_curr, 2),
                    "ema40": round(ema40_curr, 2),
                    "ema20_slope_bps": round(ema20_slope, 2),
                    "fg_index": fg_index,
                    "vol_bps": round(volatility_bps, 2)
                }
            )

        return None