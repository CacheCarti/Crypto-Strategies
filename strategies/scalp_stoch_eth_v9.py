from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.lower_extreme = 6.0
        self.lower_trigger = 14.0
        self.upper_extreme = 94.0
        self.upper_trigger = 86.0
        self.midline = 50.0
        self.cooldown_bars = 120
        self.last_trade_bar = -500

    def _calc_stoch(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        h = max(highs[-period:])
        l = min(lows[-period:])
        if h == l:
            return 50.0
        return ((closes[-1] - l) / (h - l)) * 100.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.period + 3
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        closes = ctx.closes(req_bars)

        if len(closes) < req_bars:
            return None

        curr_stoch = self._calc_stoch(highs, lows, closes, self.period)
        prev_stoch = self._calc_stoch(highs[:-1], lows[:-1], closes[:-1], self.period)

        if curr_stoch is None or prev_stoch is None:
            return None

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_stoch >= self.midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "stoch_midline_reached_long_exit",
                        "stoch": round(curr_stoch, 2),
                        "prev_stoch": round(prev_stoch, 2),
                        "price": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and curr_stoch <= self.midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "stoch_midline_reached_short_exit",
                        "stoch": round(curr_stoch, 2),
                        "prev_stoch": round(prev_stoch, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Hard cooldown between trades to prevent overtrading and fee drag
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Strict entry criteria: Deep extreme followed by a strong reversal threshold cross
        if prev_stoch <= self.lower_extreme and curr_stoch >= self.lower_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_extreme_oversold_reversal",
                    "stoch": round(curr_stoch, 2),
                    "prev_stoch": round(prev_stoch, 2),
                    "price": ctx.bar.close,
                },
            )

        if prev_stoch >= self.upper_extreme and curr_stoch <= self.upper_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_extreme_overbought_reversal",
                    "stoch": round(curr_stoch, 2),
                    "prev_stoch": round(prev_stoch, 2),
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index