from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_short_period = 20
        self.ema_long_period = 50
        self.atr_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999

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
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and keep trade count ~20-80
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        fng = ctx.features.get("fear_greed_index", 50.0)
        # Avoid extreme reversal regimes
        if fng < 25.0 or fng > 75.0:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "fng_extreme_risk_off", "fng": fng})
            return None

        # Indicator calculations
        ema20 = self._ema(closes, self.ema_short_period)
        ema20_prev = self._ema(closes[:-1], self.ema_short_period)
        ema50 = self._ema(closes, self.ema_long_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema20 is None or ema20_prev is None or ema50 is None or atr is None:
            return None

        price = ctx.bar.close
        prev_price = closes[-2]
        ema20_slope = (ema20 - ema20_prev) / ema20_prev * 10000.0

        # Volatility confirmation: ATR expansion (volatility state filter)
        atr_pct = (atr / price) * 10000.0
        if atr_pct < 20.0:  # Dead market filter
            return None

        curr_pos = ctx.position_direction()

        # Dynamic stop-loss & take-profit scaling using volatility state
        sl_bps = max(180.0, min(380.0, atr_pct * 1.5))
        tp_bps = max(300.0, min(650.0, atr_pct * 2.8))

        # ENTRY: Trigger on breakout/cross confirmed by trend and slope
        bullish_setup = (
            price > ema20 > ema50
            and ema20_slope > 1.5
            and prev_price <= closes[-1]
            and closes[-1] > ctx.bar.open
        )
        bearish_setup = (
            price < ema20 < ema50
            and ema20_slope < -1.5
            and prev_price >= closes[-1]
            and closes[-1] < ctx.bar.open
        )

        if not ctx.has_position():
            if bullish_setup:
                conf = 0.6 + min(0.35, max(0.0, (ema20_slope / 20.0)))
                return ctx.signal(
                    "long",
                    confidence=round(conf, 2),
                    stop_loss_bps=round(sl_bps, 1),
                    take_profit_bps=round(tp_bps, 1),
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_trend_continuation",
                        "fng": fng,
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "ema20_slope": round(ema20_slope, 2),
                        "atr_bps": round(atr_pct, 1),
                    },
                )
            elif bearish_setup:
                conf = 0.6 + min(0.35, max(0.0, abs(ema20_slope) / 20.0))
                return ctx.signal(
                    "short",
                    confidence=round(conf, 2),
                    stop_loss_bps=round(sl_bps, 1),
                    take_profit_bps=round(tp_bps, 1),
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_trend_continuation",
                        "fng": fng,
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "ema20_slope": round(ema20_slope, 2),
                        "atr_bps": round(atr_pct, 1),
                    },
                )
        else:
            # Active trend violation exits
            if curr_pos == "long" and (price < ema20 or ema20_slope < -0.5):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_trend_violation", "price": price, "ema20": ema20},
                )
            elif curr_pos == "short" and (price > ema20 or ema20_slope > 0.5):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_trend_violation", "price": price, "ema20": ema20},
                )

        return None