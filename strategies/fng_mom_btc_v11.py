from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold for 1h swing
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

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

        # Cooldown guard: enforce rest period after any trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        ema20 = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)
        atr14 = self._atr(highs, lows, closes, 14)

        if ema20 is None or ema20_prev is None or ema50 is None or atr14 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        ema20_slope_bps = ((ema20 - ema20_prev) / ema20_prev) * 10000.0

        # Sentiment regime gating: stand down during market panic (<25) or euphoria (>75)
        fng = float(ctx.features.get("fear_greed_index", 50.0))
        sentiment_neutral = 25.0 <= fng <= 75.0

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Regime breakdown exit: extreme sentiment flips trigger early de-risking
            if not sentiment_neutral:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "sentiment_extreme_reached",
                    "fear_greed": fng,
                    "bars_held": bars_held,
                })

            # Trend exhaustion or counter-trend crossover exit
            if pos_dir == "long" and (curr_close < ema20 or ema20_slope_bps < -3.0):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_trend_exhaustion",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema20_slope_bps": ema20_slope_bps,
                })
            elif pos_dir == "short" and (curr_close > ema20 or ema20_slope_bps > 3.0):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_trend_exhaustion",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema20_slope_bps": ema20_slope_bps,
                })
            return None

        # No open position: Evaluate high-conviction entries gated by sentiment
        if not sentiment_neutral:
            return None

        # Filter out extreme crisis regime from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Require minimum volatility buffer to avoid false breakouts
        min_atr_pct = 0.0035  # At least 35 bps ATR
        if (atr14 / curr_close) < min_atr_pct:
            return None

        # High-conviction Long:
        # 1. Price above both EMA20 and EMA50 (structural uptrend)
        # 2. Fresh transition: previous bar was below or close to EMA20, now breaking above with strength
        # 3. EMA20 distinctly rising
        long_setup = (
            curr_close > ema20 > ema50 and
            prev_close <= ema20 * 1.001 and
            ema20_slope_bps > 5.0
        )

        if long_setup:
            self.entry_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "bullish_ema_transition_sentiment_safe",
                "fear_greed": fng,
                "ema20": ema20,
                "ema50": ema50,
                "ema20_slope_bps": ema20_slope_bps,
                "price": curr_close,
            })

        # High-conviction Short:
        # 1. Price below both EMA20 and EMA50 (structural downtrend)
        # 2. Fresh transition: previous bar was above or close to EMA20, now breaking down
        # 3. EMA20 distinctly falling
        short_setup = (
            curr_close < ema20 < ema50 and
            prev_close >= ema20 * 0.999 and
            ema20_slope_bps < -5.0
        )

        if short_setup:
            self.entry_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "bearish_ema_transition_sentiment_safe",
                "fear_greed": fng,
                "ema20": ema20,
                "ema50": ema50,
                "ema20_slope_bps": ema20_slope_bps,
                "price": curr_close,
            })

        return None