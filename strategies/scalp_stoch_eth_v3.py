from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 2400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 24
        self.lower_threshold = 8.0
        self.upper_threshold = 92.0
        self.midline = 50.0
        self.cooldown_bars = 60
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _calc_stoch(self, highs, lows, closes, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.period + 3)
        highs = ctx.highs(self.period + 3)
        lows = ctx.lows(self.period + 3)

        if len(closes) < self.period + 3:
            return None

        # Compute current and previous stochastic %K
        curr_k = self._calc_stoch(highs, lows, closes, self.period)
        prev_k = self._calc_stoch(highs[:-1], lows[:-1], closes[:-1], self.period)

        if curr_k is None or prev_k is None:
            return None

        current_price = ctx.bar.close

        # Manage open positions: exit at stochastic midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_k >= self.midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_stoch_midline_reached",
                        "stoch_k": curr_k,
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and curr_k <= self.midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_stoch_midline_reached",
                        "stoch_k": curr_k,
                        "price": current_price,
                    },
                )
            return None

        # Enforce strict cooldown after last exit and last entry
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Long Setup: Deep oversold threshold breakout back upward
        if prev_k <= self.lower_threshold and curr_k > self.lower_threshold:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_oversold_cross_up",
                    "curr_k": curr_k,
                    "prev_k": prev_k,
                    "price": current_price,
                },
            )

        # Short Setup: Deep overbought threshold breakout back downward
        if prev_k >= self.upper_threshold and curr_k < self.upper_threshold:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_overbought_cross_down",
                    "curr_k": curr_k,
                    "prev_k": prev_k,
                    "price": current_price,
                },
            )

        return None