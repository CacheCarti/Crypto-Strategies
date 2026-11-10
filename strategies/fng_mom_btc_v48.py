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
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 40
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < 45:
            return None

        # Calculate indicators
        ema20_current = self._ema(closes, self.ema_fast_period)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_period)
        ema40 = self._ema(closes, self.ema_slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema20_current is None or ema20_prev is None or ema40 is None or atr is None:
            return None

        price = ctx.bar.close
        fng = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Position management
        if ctx.has_position():
            pos = ctx.position
            pos_dir = pos["direction"] if pos else ctx.position_direction()
            
            # Trend exhaustion or breakdown exits
            if pos_dir == "long":
                if price < ema20_current or ema20_current < ema20_prev or fng > 80:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "exit_long_ema_cross_or_fng_extreme",
                            "price": price,
                            "ema20": ema20_current,
                            "fng": fng,
                        }
                    )
            elif pos_dir == "short":
                if price > ema20_current or ema20_current > ema20_prev or fng < 20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "exit_short_ema_cross_or_fng_extreme",
                            "price": price,
                            "ema20": ema20_current,
                            "fng": fng,
                        }
                    )
            return None

        # Cooldown guard to avoid over-trading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime and Sentiment Filters:
        # 1. Avoid violent extremes (Fear & Greed between 25 and 75)
        # 2. Skip crisis regimes where momentum suffers from erratic wicks
        if fng < 28 or fng > 72:
            return None
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Clean trend alignment condition
        prev_close = closes[-2]
        ema_slope = ema20_current - ema20_prev
        atr_threshold = atr * 0.05

        # Long: Fresh crossover or push above EMA20, confirmed above EMA40 with rising EMA20
        if (
            price > ema20_current
            and prev_close <= ema20_prev * 1.001
            and ema20_current > ema40
            and ema_slope > atr_threshold
            and fng >= 35
        ):
            confidence = min(0.85, 0.55 + (fng - 35) / 100.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_momentum_fng_normal",
                    "price": price,
                    "ema20": ema20_current,
                    "ema40": ema40,
                    "ema_slope": ema_slope,
                    "fng": fng,
                    "regime": market_regime,
                }
            )

        # Short: Fresh breakdown below EMA20, confirmed below EMA40 with falling EMA20
        if (
            price < ema20_current
            and prev_close >= ema20_prev * 0.999
            and ema20_current < ema40
            and ema_slope < -atr_threshold
            and fng <= 65
        ):
            confidence = min(0.85, 0.55 + (65 - fng) / 100.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_momentum_fng_normal",
                    "price": price,
                    "ema20": ema20_current,
                    "ema40": ema40,
                    "ema_slope": ema_slope,
                    "fng": fng,
                    "regime": market_regime,
                }
            )

        return None