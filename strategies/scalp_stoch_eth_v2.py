from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 36
        self.oversold = 6.0
        self.overbought = 94.0
        self.midline = 50.0
        self.cooldown_bars = 120
        self.last_exit_bar = -1000

    def _stochastic(self, highs: list, lows: list, closes: list, period: int, offset: int = 0) -> Optional[float]:
        end_idx = len(closes) - offset
        start_idx = end_idx - period
        if start_idx < 0 or end_idx > len(closes):
            return None
        sub_highs = highs[start_idx:end_idx]
        sub_lows = lows[start_idx:end_idx]
        highest = max(sub_highs)
        lowest = min(sub_lows)
        if highest == lowest:
            return 50.0
        curr_close = closes[end_idx - 1]
        return ((curr_close - lowest) / (highest - lowest)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        highs = ctx.highs(self.period + 5)
        lows = ctx.lows(self.period + 5)
        closes = ctx.closes(self.period + 5)

        if len(closes) < self.period + 2:
            return None

        curr_k = self._stochastic(highs, lows, closes, self.period, offset=0)
        prev_k = self._stochastic(highs, lows, closes, self.period, offset=1)

        if curr_k is None or prev_k is None:
            return None

        # Exit open positions at stochastic midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_k >= self.midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_long_midline_exit", "stoch_k": round(curr_k, 2), "price": ctx.bar.close}
                )
            elif pos_dir == "short" and curr_k <= self.midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_short_midline_exit", "stoch_k": round(curr_k, 2), "price": ctx.bar.close}
                )
            return None

        # Hard cooldown enforcement between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: Severe oversold exhaustion rebound
        if prev_k <= self.oversold and curr_k > self.oversold:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_oversold_reversal",
                    "stoch_curr": round(curr_k, 2),
                    "stoch_prev": round(prev_k, 2),
                    "price": ctx.bar.close
                }
            )

        # Short Entry: Severe overbought exhaustion reversal
        if prev_k >= self.overbought and curr_k < self.overbought:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_overbought_reversal",
                    "stoch_curr": round(curr_k, 2),
                    "stoch_prev": round(prev_k, 2),
                    "price": ctx.bar.close
                }
            )

        return None