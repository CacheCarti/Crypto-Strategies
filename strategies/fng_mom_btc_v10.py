from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentMomentumFilter(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _ema_series(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        res = [ema]
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
            res.append(ema)
        return res

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme sentiment traps (crowded long/short reversals)
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        if fg_index <= 25.0 or fg_index >= 75.0:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "extreme_sentiment_standdown", "fg_index": fg_index}
                )
            return None

        # Volatility regime confirmation via normalized ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or closes[-1] == 0:
            return None
        natr_bps = (atr / closes[-1]) * 10000.0
        # Stand down in dead flat or chaotic meltdown volatility
        if natr_bps < 40.0 or natr_bps > 450.0:
            return None

        ema20_vals = self._ema_series(closes, self.fast_period)
        ema50_vals = self._ema_series(closes, self.slow_period)
        if not ema20_vals or len(ema20_vals) < 3 or not ema50_vals or len(ema50_vals) < 1:
            return None

        ema20_curr = ema20_vals[-1]
        ema20_prev = ema20_vals[-2]
        ema50_curr = ema50_vals[-1]
        price = closes[-1]
        price_prev = closes[-2]

        # Momentum slopes
        ema20_rising = ema20_curr > ema20_prev
        ema20_falling = ema20_curr < ema20_prev

        # Long breakout/cross setup: trend aligned, price newly crossing/bouncing above rising EMA20
        fresh_cross_above = (price > ema20_curr) and (price_prev <= ema20_prev or price > ema50_curr > ema20_prev)
        bullish_structure = (price > ema20_curr) and (ema20_curr > ema50_curr) and ema20_rising and fresh_cross_above

        # Short breakdown/cross setup: trend aligned, price newly crossing/dropping below falling EMA20
        fresh_cross_below = (price < ema20_curr) and (price_prev >= ema20_prev or price < ema50_curr < ema20_prev)
        bearish_structure = (price < ema20_curr) and (ema20_curr < ema50_curr) and ema20_falling and fresh_cross_below

        pos_dir = ctx.position_direction()

        # Dynamic confidence based on balanced sentiment center
        sentiment_centrality = 1.0 - abs(fg_index - 50.0) / 25.0
        base_confidence = 0.55 + 0.35 * sentiment_centrality

        if bullish_structure and pos_dir != "long":
            return ctx.signal(
                "long",
                confidence=round(base_confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_trend_reacceleration",
                    "price": round(price, 2),
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "fg_index": fg_index,
                    "natr_bps": round(natr_bps, 1),
                }
            )

        if bearish_structure and pos_dir != "short":
            return ctx.signal(
                "short",
                confidence=round(base_confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_trend_breakdown",
                    "price": round(price, 2),
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "fg_index": fg_index,
                    "natr_bps": round(natr_bps, 1),
                }
            )

        # Dynamic exit if trend breaks while holding
        if pos_dir == "long" and price < ema20_curr and ema20_falling:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={"reason": "exit_long_ema20_loss", "price": round(price, 2), "ema20": round(ema20_curr, 2)}
            )

        if pos_dir == "short" and price > ema20_curr and ema20_rising:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={"reason": "exit_short_ema20_reclaim", "price": round(price, 2), "ema20": round(ema20_curr, 2)}
            )

        return None