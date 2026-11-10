from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackRejection(Strategy):
    METADATA = {
        "name": "EthEmaPullbackRejection",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_period = 6
        self.last_trade_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        highs = ctx.highs(3)
        lows = ctx.lows(3)

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage open position exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_ema20_breakdown",
                    "price": curr_close,
                    "ema20": ema20_curr
                })
            elif direction == "short" and curr_close > ema20_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_ema20_breakout",
                    "price": curr_close,
                    "ema20": ema20_curr
                })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Long Trigger: 
        # 1. Clear uptrend: EMA20 > EMA50, EMA50 trending up or stable
        # 2. Touch/Dip event: prev or curr candle dipped below/to EMA20
        # 3. Clean reclaim event: previous close <= prev EMA20 AND current close > current EMA20
        long_trend = (ema20_curr > ema50_curr) and (ema50_curr >= ema50_prev * 0.9995)
        touched_ema20_long = (curr_low <= ema20_curr * 1.001) or (prev_low <= ema20_prev * 1.001)
        reclaimed_ema20_long = (prev_close <= ema20_prev * 1.0005) and (curr_close > ema20_curr)

        if long_trend and touched_ema20_long and reclaimed_ema20_long:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "price": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr
                }
            )

        # Short Trigger:
        # 1. Clear downtrend: EMA20 < EMA50, EMA50 trending down or stable
        # 2. Touch/Rip event: prev or curr candle poked above/to EMA20
        # 3. Clean rejection event: previous close >= prev EMA20 AND current close < current EMA20
        short_trend = (ema20_curr < ema50_curr) and (ema50_curr <= ema50_prev * 1.0005)
        touched_ema20_short = (curr_high >= ema20_curr * 0.999) or (prev_high >= ema20_prev * 0.999)
        rejected_ema20_short = (prev_close >= ema20_prev * 0.9995) and (curr_close < ema20_curr)

        if short_trend and touched_ema20_short and rejected_ema20_short:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_rejection_short",
                    "price": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr
                }
            )

        return None