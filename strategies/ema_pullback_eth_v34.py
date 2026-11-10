from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackPrecision(Strategy):
    METADATA = {
        "name": "EthEmaPullbackPrecision",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours average swing hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.fast_ema_val: Optional[float] = None
        self.slow_ema_val: Optional[float] = None

    def _ema(self, values, period: int) -> Optional[float]:
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
        highs = ctx.highs(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        c_now = closes[-1]
        c_prev = closes[-2]
        l_prev = lows[-2]
        h_prev = highs[-2]

        # Calculate slope of EMA50 to guarantee trend conviction
        ema50_prev = self._ema(closes[:-1], self.slow_period)
        ema50_slope = (ema50 - ema50_prev) / ema50_prev if ema50_prev else 0.0

        # Position management: exit on structural EMA20 violation
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and c_now < ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_ema20_break", "price": c_now, "ema20": ema20}
                )
            elif pos_dir == "short" and c_now > ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_ema20_break", "price": c_now, "ema20": ema20}
                )
            return None

        # Long Entry: Strong macro uptrend (EMA20 > EMA50, positive slope)
        # Event trigger: previous bar tested/dipped below EMA20 (low <= EMA20) and closed near/below it,
        # but current bar decisively reclaims and closes firmly above EMA20.
        bullish_structure = (c_now > ema50) and (ema20 > ema50) and (ema50_slope > 0.0001)
        long_pullback_reclaim = (l_prev <= ema20) and (c_prev <= ema20 * 1.002) and (c_now > ema20 * 1.001)

        if bullish_structure and long_pullback_reclaim:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_bull",
                    "c_now": c_now,
                    "c_prev": c_prev,
                    "ema20": ema20,
                    "ema50": ema50,
                    "ema50_slope": ema50_slope,
                }
            )

        # Short Entry: Strong macro downtrend (EMA20 < EMA50, negative slope)
        # Event trigger: previous bar popped up to touch/exceed EMA20, current bar rejects and closes below.
        bearish_structure = (c_now < ema50) and (ema20 < ema50) and (ema50_slope < -0.0001)
        short_pullback_rejection = (h_prev >= ema20) and (c_prev >= ema20 * 0.998) and (c_now < ema20 * 0.999)

        if bearish_structure and short_pullback_rejection:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reject_bear",
                    "c_now": c_now,
                    "c_prev": c_prev,
                    "ema20": ema20,
                    "ema50": ema50,
                    "ema50_slope": ema50_slope,
                }
            )

        return None