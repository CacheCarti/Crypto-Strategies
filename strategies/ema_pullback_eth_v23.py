from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackStrategy(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
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
        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)

        if len(closes) < self.slow_period + 2:
            return None

        # Compute indicator state
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        price = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and price < ema20_curr and prev_close >= ema20_prev:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "long_exit_cross_below_ema20", "price": price, "ema20": ema20_curr}
                )
            elif pos_dir == "short" and price > ema20_curr and prev_close <= ema20_prev:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "short_exit_cross_above_ema20", "price": price, "ema20": ema20_curr}
                )
            return None

        # Post-exit cooldown gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime and market gating
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Uptrend: EMA20 comfortably above EMA50, and EMA50 sloping up or steady
        uptrend = (ema20_curr > ema50_curr) and (price > ema50_curr)
        # Pullback and reclaim logic:
        # Prior bar or current bar dipped to/below EMA20, but current close reclaims above EMA20
        dipped_below_long = (prev_low <= ema20_prev) or (curr_low <= ema20_curr)
        reclaimed_long = (price > ema20_curr) and (prev_close <= ema20_prev or curr_low <= ema20_curr * 0.999)

        if uptrend and dipped_below_long and reclaimed_long:
            trend_strength = (ema20_curr - ema50_curr) / ema50_curr
            conf = min(0.9, max(0.6, 0.6 + trend_strength * 20.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema_pullback_reclaim_uptrend",
                    "price": price,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "prev_low": round(prev_low, 2)
                }
            )

        # Downtrend: EMA20 below EMA50, price below EMA50
        downtrend = (ema20_curr < ema50_curr) and (price < ema50_curr)
        # Pullback and reject logic:
        # Prior bar or current bar tested/poked above EMA20, but current close rejects back below EMA20
        tested_above_short = (prev_high >= ema20_prev) or (curr_high >= ema20_curr)
        rejected_short = (price < ema20_curr) and (prev_close >= ema20_prev or curr_high >= ema20_curr * 1.001)

        if downtrend and tested_above_short and rejected_short:
            trend_strength = (ema50_curr - ema20_curr) / ema50_curr
            conf = min(0.9, max(0.6, 0.6 + trend_strength * 20.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema_pullback_reject_downtrend",
                    "price": price,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "prev_high": round(prev_high, 2)
                }
            )

        return None