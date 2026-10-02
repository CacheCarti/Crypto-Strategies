from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.oversold_thresh = 6.0
        self.overbought_thresh = 94.0
        self.long_recovery_thresh = 10.0
        self.short_recovery_thresh = 90.0
        self.cooldown_bars = 100
        self.last_trade_bar = -999

    def _stochastic(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        hh = max(highs[-period:])
        ll = min(lows[-period:])
        if hh == ll:
            return 50.0
        return ((closes[-1] - ll) / (hh - ll)) * 100.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        highs = ctx.highs(self.period + 3)
        lows = ctx.lows(self.period + 3)

        if len(closes) < self.period + 3:
            return None

        stoch_now = self._stochastic(highs, lows, closes, self.period)
        stoch_prev = self._stochastic(highs[:-1], lows[:-1], closes[:-1], self.period)

        if stoch_now is None or stoch_prev is None:
            return None

        # Position exits at mid-band normalization
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and stoch_now >= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "stoch_midline_exit_long",
                        "stoch_now": round(stoch_now, 2),
                        "price": ctx.bar.close,
                    },
                )
            if direction == "short" and stoch_now <= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "stoch_midline_exit_short",
                        "stoch_now": round(stoch_now, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Hard cooldown check after prior trades / exits
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Long entry: deep oversold flush recovering upward
        if stoch_prev <= self.oversold_thresh and stoch_now >= self.long_recovery_thresh:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_oversold_reversal",
                    "stoch_now": round(stoch_now, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        # Short entry: deep overbought surge turning downward
        if stoch_prev >= self.overbought_thresh and stoch_now <= self.short_recovery_thresh:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_overbought_reversal",
                    "stoch_now": round(stoch_now, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index