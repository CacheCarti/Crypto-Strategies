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
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 16

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]

        ema20_now = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema50_now = self._ema(closes, self.ema_slow_period)

        if ema20_now is None or ema20_prev is None or ema50_now is None:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        atr_pct = (atr / current_close) if atr else 0.01

        # Dynamic SL/TP anchored on ATR volatility
        sl_bps = max(150.0, min(350.0, atr_pct * 1.5 * 10000))
        tp_bps = sl_bps * 1.8

        fng = ctx.features.get("fear_greed_index", 50.0)

        # Existing position management: structural exit on EMA crossover loss
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close < ema20_now:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_break_under_ema20",
                            "close": current_close,
                            "ema20": ema20_now,
                            "fng": fng
                        }
                    )
            elif pos_dir == "short":
                if current_close > ema20_now:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_break_above_ema20",
                            "close": current_close,
                            "ema20": ema20_now,
                            "fng": fng
                        }
                    )
            return None

        # Stand down at sentiment extremes (<25 or >75) where mean reversion is violent
        if fng <= 25.0 or fng >= 75.0:
            return None

        # Market regime filter
        mkt_regime = ctx.market.get("regime", "NORMAL")
        if mkt_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry triggers gated on fresh crossover transitions
        ema20_slope = (ema20_now - ema20_prev) / ema20_prev

        # Long Trigger: Price crossing above EMA20, EMA20 > EMA50, positive slope
        long_cross = (prev_close <= ema20_prev) and (current_close > ema20_now)
        if long_cross and ema20_slope > 0.0003 and current_close > ema50_now:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ema_cross_moderate_fng",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "ema20_slope": ema20_slope,
                    "atr_bps": atr_pct * 10000
                }
            )

        # Short Trigger: Price crossing below EMA20, EMA20 < EMA50, negative slope
        short_cross = (prev_close >= ema20_prev) and (current_close < ema20_now)
        if short_cross and ema20_slope < -0.0003 and current_close < ema50_now:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ema_cross_moderate_fng",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "ema20_slope": ema20_slope,
                    "atr_bps": atr_pct * 10000
                }
            )

        return None