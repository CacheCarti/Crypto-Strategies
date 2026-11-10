from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackTrend(Strategy):
    METADATA = {
        "name": "EthEmaPullbackTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Compute current and prior EMA values
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None:
            return None

        price = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and price < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_ema20_cross_below", "price": price, "ema20": ema20_curr}
                )
            elif direction == "short" and price > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_ema20_cross_above", "price": price, "ema20": ema20_curr}
                )
            return None

        # Cooldown guard after exits to prevent churn and friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Trend filter: EMA slope & alignment
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long pullback setup:
        # Price and EMA20 above EMA50, price touched/dipped below EMA20 (this bar or prev bar),
        # and current close confirms bounce back above EMA20.
        uptrend = ema20_curr > ema50_curr and price > ema50_curr and trend_regime != "bear"
        pullback_tested_long = (curr_low <= ema20_curr or prev_low <= ema20_prev)
        reclaimed_above_long = price > ema20_curr and prev_close <= ema20_prev

        if uptrend and pullback_tested_long and reclaimed_above_long:
            conf = 0.75 if trend_regime == "bull" else 0.60
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pullback_bounce_above_ema20_uptrend",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "trend_regime": trend_regime,
                }
            )

        # Short pullback setup:
        # Price and EMA20 below EMA50, price touched/poked above EMA20,
        # and current close rejects back below EMA20.
        downtrend = ema20_curr < ema50_curr and price < ema50_curr and trend_regime != "bull"
        pullback_tested_short = (curr_high >= ema20_curr or prev_high >= ema20_prev)
        reclaimed_below_short = price < ema20_curr and prev_close >= ema20_prev

        if downtrend and pullback_tested_short and reclaimed_below_short:
            conf = 0.75 if trend_regime == "bear" else 0.60
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pullback_rejection_below_ema20_downtrend",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "trend_regime": trend_regime,
                }
            )

        return None