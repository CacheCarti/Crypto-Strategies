from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdaptive(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 4
        self.last_trade_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(5)
        highs = ctx.highs(5)

        if len(closes) < self.slow_period + 2:
            return None

        # Compute indicators
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_exit_cross_below_ema20",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr,
                    }
                )
            elif pos_dir == "short" and curr_close > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_exit_cross_above_ema20",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr,
                    }
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Trend and pullback trigger conditions
        # Long Setup: Clear uptrend (EMA20 > EMA50, price > EMA50), touched/dipped below EMA20 recently and closed back above
        uptrend = (ema20_curr > ema50_curr) and (curr_close > ema50_curr)
        pullback_long = (prev_low <= ema20_prev or prev_close <= ema20_prev) and (curr_close > ema20_curr)

        if uptrend and pullback_long:
            self.last_trade_bar = ctx.bar_index
            trend_margin = (ema20_curr - ema50_curr) / ema50_curr
            conf = min(0.9, max(0.55, 0.6 + trend_margin * 10.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pullback_reclaim_ema20_uptrend",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "regime": regime,
                }
            )

        # Short Setup: Clear downtrend (EMA20 < EMA50, price < EMA50), touched/spiked above EMA20 recently and closed back below
        downtrend = (ema20_curr < ema50_curr) and (curr_close < ema50_curr)
        pullback_short = (prev_high >= ema20_prev or prev_close >= ema20_prev) and (curr_close < ema20_curr)

        if downtrend and pullback_short:
            self.last_trade_bar = ctx.bar_index
            trend_margin = (ema50_curr - ema20_curr) / ema50_curr
            conf = min(0.9, max(0.55, 0.6 + trend_margin * 10.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pullback_reject_ema20_downtrend",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "regime": regime,
                }
            )

        return None