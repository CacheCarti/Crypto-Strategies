from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class EmaMomentumScalp(Strategy):
    METADATA = {
        "name": "EMA Momentum Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 5
        self.slow_period = 13
        self.trend_period = 50
        self.cooldown_bars = 120
        self.last_trade_bar = -200

    def _ema(self, values: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.trend_period + 5)
        if len(closes) < self.trend_period + 2:
            return None

        # Hard multi-bar cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Current indicators
        fast_curr = self._ema(closes, self.fast_period)
        slow_curr = self._ema(closes, self.slow_period)
        trend_curr = self._ema(closes, self.trend_period)

        # Previous indicators for transition check & slope
        prev_closes = closes[:-1]
        fast_prev = self._ema(prev_closes, self.fast_period)
        slow_prev = self._ema(prev_closes, self.slow_period)
        trend_prev = self._ema(prev_closes, self.trend_period)

        if (
            fast_curr is None
            or slow_curr is None
            or trend_curr is None
            or fast_prev is None
            or slow_prev is None
            or trend_prev is None
        ):
            return None

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and fast_curr < slow_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_bearish_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "close": round(closes[-1], 2),
                    },
                )
            elif pos_dir == "short" and fast_curr > slow_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_bullish_cross_exit",
                        "fast_ema": round(fast_curr, 2),
                        "slow_ema": round(slow_curr, 2),
                        "close": round(closes[-1], 2),
                    },
                )
            return None

        # Strict entry conditions: EMA cross + above/below EMA50 + aligned EMA50 slope
        bullish_cross = (
            fast_prev <= slow_prev
            and fast_curr > slow_curr
            and closes[-1] > trend_curr
            and trend_curr > trend_prev
        )

        bearish_cross = (
            fast_prev >= slow_prev
            and fast_curr < slow_curr
            and closes[-1] < trend_curr
            and trend_curr < trend_prev
        )

        if bullish_cross:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=180.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "ema5_cross_ema13_bull_uptrend",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "trend_ema": round(trend_curr, 2),
                    "close": round(closes[-1], 2),
                },
            )

        if bearish_cross:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=180.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "ema5_cross_ema13_bear_downtrend",
                    "fast_ema": round(fast_curr, 2),
                    "slow_ema": round(slow_curr, 2),
                    "trend_ema": round(trend_curr, 2),
                    "close": round(closes[-1], 2),
                },
            )

        return None