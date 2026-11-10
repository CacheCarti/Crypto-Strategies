from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdaptive(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 4
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
        closes = ctx.closes(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Calculate EMAs for current bar and previous bar
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if None in (ema20_curr, ema50_curr, ema20_prev, ema50_prev):
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_ema20_cross_below",
                    "close": curr_close,
                    "ema20": ema20_curr
                })
            elif direction == "short" and curr_close > ema20_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_ema20_cross_above",
                    "close": curr_close,
                    "ema20": ema20_curr
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check market regime to avoid entering during crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup:
        # 1. Uptrend: EMA20 > EMA50 and close > EMA50
        # 2. Pullback trigger: prior bar breached or touched EMA20 (or current low touched EMA20),
        #    and current bar confirms reversal by closing firmly above EMA20.
        uptrend = ema20_curr > ema50_curr and curr_close > ema50_curr
        long_pullback_touch = (prev_low <= ema20_prev) or (curr_low <= ema20_curr)
        long_reclaim = curr_close > ema20_curr and prev_close <= ema20_prev

        if uptrend and long_pullback_touch and long_reclaim:
            confidence = 0.75 if curr_close > (ema20_curr * 1.002) else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "market_regime": market_regime
                }
            )

        # Short Setup:
        # 1. Downtrend: EMA20 < EMA50 and close < EMA50
        # 2. Pullback trigger: prior bar pierced or touched EMA20 (or current high touched EMA20),
        #    and current bar confirms rejection by closing firmly below EMA20.
        downtrend = ema20_curr < ema50_curr and curr_close < ema50_curr
        short_pullback_touch = (prev_high >= ema20_prev) or (curr_high >= ema20_curr)
        short_reclaim = curr_close < ema20_curr and prev_close >= ema20_prev

        if downtrend and short_pullback_touch and short_reclaim:
            confidence = 0.75 if curr_close < (ema20_curr * 0.998) else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_rejection_short",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "market_regime": market_regime
                }
            )

        return None