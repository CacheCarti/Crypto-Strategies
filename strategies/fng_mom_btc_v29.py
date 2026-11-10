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
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 10
        self.ema_slow_period = 25
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(25)
        lows = ctx.lows(25)

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_fast_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        ema_slow_prev = self._ema(closes[:-1], self.ema_slow_period)

        if ema_fast is None or ema_fast_prev is None or ema_slow is None or ema_slow_prev is None:
            return None

        # Check existing positions for trend exhaustion or reversal
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and ema_fast < ema_slow:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_bear_cross", "ema_fast": ema_fast, "ema_slow": ema_slow}
                )
            elif pos_dir == "short" and ema_fast > ema_slow:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_bull_cross", "ema_fast": ema_fast, "ema_slow": ema_slow}
                )
            return None

        # Cooldown guard after closing a position
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment regime gating: stand down at violent extremes (<25 extreme fear, >75 extreme greed)
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        if fg_index < 25.0 or fg_index > 75.0:
            return None

        # Market crisis guard
        crisis_score = float(ctx.market.get("crisis_score", 0.0))
        if crisis_score > 0.7:
            return None

        # Volatility check: require ATR to compute dynamic SL/TP
        atr = self._atr(highs, lows, closes, self.atr_period)
        price = ctx.bar.close
        if atr is None or price <= 0:
            return None

        # Transition trigger: Detect moving average crosses (prevents firing on every bar of a trend)
        bull_cross = (ema_fast_prev <= ema_slow_prev) and (ema_fast > ema_slow)
        bear_cross = (ema_fast_prev >= ema_slow_prev) and (ema_fast < ema_slow)

        atr_pct = (atr / price) * 10000.0
        sl_bps = max(150.0, min(350.0, atr_pct * 1.5))
        tp_bps = max(300.0, min(700.0, atr_pct * 3.0))

        if bull_cross and price > ema_fast:
            # Scaled confidence: higher confidence when sentiment is firmly bullish-neutral (45-65)
            confidence = 0.65 if (45.0 <= fg_index <= 65.0) else 0.55
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bull_cross_sentiment_favorable",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed": fg_index,
                    "atr": atr,
                }
            )

        if bear_cross and price < ema_fast:
            confidence = 0.65 if (35.0 <= fg_index <= 55.0) else 0.55
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bear_cross_sentiment_favorable",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed": fg_index,
                    "atr": atr,
                }
            )

        return None