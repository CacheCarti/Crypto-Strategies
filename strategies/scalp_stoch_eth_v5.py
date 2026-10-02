from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 18
        self.oversold = 14.0
        self.overbought = 86.0
        self.cooldown_bars = 20
        self.last_exit_bar = -100

    def _stochastic(self, highs, lows, closes, period):
        if len(closes) < period:
            return None
        h_slice = highs[-period:]
        l_slice = lows[-period:]
        highest = max(h_slice)
        lowest = min(l_slice)
        if highest == lowest:
            return 50.0
        return ((closes[-1] - lowest) / (highest - lowest)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)
        closes = ctx.closes(self.period + 2)

        if len(closes) < self.period + 2:
            return None

        stoch_curr = self._stochastic(highs, lows, closes, self.period)
        stoch_prev = self._stochastic(highs[:-1], lows[:-1], closes[:-1], self.period)

        if stoch_curr is None or stoch_prev is None:
            return None

        has_pos = ctx.has_position()
        direction = ctx.position_direction()

        # Exit logic if in position: exit when reaching the 50 midline
        if has_pos:
            if direction == "long" and stoch_curr >= 50.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "stoch_midline_reached_long",
                        "stoch": round(stoch_curr, 2),
                        "price": ctx.bar.close,
                    },
                )
            elif direction == "short" and stoch_curr <= 50.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "stoch_midline_reached_short",
                        "stoch": round(stoch_curr, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long entry: oversold recovery turning up
        if stoch_prev <= self.oversold and stoch_curr > stoch_prev:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_oversold_turnup",
                    "stoch": round(stoch_curr, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close,
                },
            )

        # Short entry: overbought rejection turning down
        if stoch_prev >= self.over