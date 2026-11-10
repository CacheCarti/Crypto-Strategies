from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackEvent(Strategy):
    METADATA = {
        "name": "EthEmaPullbackEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_bars_held = 3

    def _ema(self, values: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Compute current and previous EMAs to detect trend and pullbacks cleanly
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        prev_ema20 = self._ema(closes[:-1], self.fast_period)
        prev_ema50 = self._ema(closes[:-1], self.slow_period)

        if ema20 is None or ema50 is None or prev_ema20 is None or prev_ema50 is None:
            return None

        price = ctx.bar.close
        high = ctx.bar.high
        low = ctx.bar.low
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage open position exits first
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos_data = ctx.position or {}
            entry_price = pos_data.get("entry_price", price)
            
            # Position exit rule: close crossing back past EMA20 against trend
            if pos_dir == "long":
                if price < ema20 and prev_close >= prev_ema20:
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "exit_cross_below_ema20",
                        "price": price,
                        "ema20": ema20,
                        "entry_price": entry_price
                    })
            elif pos_dir == "short":
                if price > ema20 and prev_close <= prev_ema20:
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "exit_cross_above_ema20",
                        "price": price,
                        "ema20": ema20,
                        "entry_price": entry_price
                    })
            return None

        # Hard Cooldown filter to prevent rapid re-entry / churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Strict event boundary for Long:
        # 1. Structural trend: EMA20 > EMA50 and Price > EMA50
        # 2. Touch/Dip event: Previous low was below/at previous EMA20 OR current low touched EMA20
        # 3. Confirmation: Current close reclaimed above EMA20, with prior close not deeply extended
        long_trend = ema20 > ema50 and price > ema50
        long_dip = (prev_low <= prev_ema20 or low <= ema20)
        long_reclaim = price > ema20 and prev_close <= (prev_ema20 * 1.002)

        if long_trend and long_dip and long_reclaim:
            trend_margin = (ema20 - ema50) / ema50
            conf = min(0.85, max(0.55, 0.60 + trend_margin * 10.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_pullback_reclaim_ema20",
                    "price": price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "regime": market_regime,
                }
            )

        # Strict event boundary for Short:
        # 1. Structural trend: EMA20 < EMA50 and Price < EMA50
        # 2. Touch/Rip event: Previous high was above/at previous EMA20 OR current high touched EMA20
        # 3. Confirmation: Current close fell back below EMA20
        short_trend = ema20 < ema50 and price < ema50
        short_rip = (prev_high >= prev_ema20 or high >= ema20)
        short_reject = price < ema20 and prev_close >= (prev_ema20 * 0.998)

        if short_trend and short_rip and short_reject:
            trend_margin = (ema50 - ema20) / ema50
            conf = min(0.85, max(0.55, 0.60 + trend_margin * 10.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_pullback_reject_ema20",
                    "price": price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "regime": market_regime,
                }
            )

        return None