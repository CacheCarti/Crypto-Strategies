from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackRegimeStrategy(Strategy):
    METADATA = {
        "name": "EthEmaPullbackRegimeStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate Indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        prev_ema20 = self._ema(closes[:-1], self.fast_period)

        if ema20 is None or ema50 is None or prev_ema20 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        # Exit conditions if holding position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_exit_cross_below_ema20",
                        "price": curr_close,
                        "ema20": ema20,
                    }
                )
            elif direction == "short" and curr_close > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_exit_cross_above_ema20",
                        "price": curr_close,
                        "ema20": ema20,
                    }
                )
            return None

        # Cooldown guard after exit to prevent churning and overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate to fix prior ETH pullback failures: avoid crisis and chopped meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Entry: Strong uptrend alignment (EMA20 > EMA50, price above EMA50, trend regime supportive)
        # Pullback: Previous bar dipped to/below EMA20, current bar closes back firmly above EMA20
        long_trend = (ema20 > ema50) and (curr_close > ema50) and (trend_regime in ("bull", "neutral"))
        long_pullback = (prev_low <= prev_ema20 or prev_close <= prev_ema20) and (curr_close > ema20)

        if long_trend and long_pullback:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_uptrend",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "prev_low": prev_low,
                    "trend_regime": trend_regime,
                }
            )

        # Short Entry: Strong downtrend alignment (EMA20 < EMA50, price below EMA50, trend regime supportive)
        # Pullback: Previous bar bounced to/above EMA20, current bar closes back firmly below EMA20
        short_trend = (ema20 < ema50) and (curr_close < ema50) and (trend_regime in ("bear", "neutral"))
        short_pullback = (prev_high >= prev_ema20 or prev_close >= prev_ema20) and (curr_close < ema20)

        if short_trend and short_pullback:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_rejection_downtrend",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "prev_high": prev_high,
                    "trend_regime": trend_regime,
                }
            )

        return None