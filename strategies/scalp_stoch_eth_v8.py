from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.lower_thresh = 8.0
        self.upper_thresh = 92.0
        self.cooldown_bars = 80
        self.last_exit_bar = -100

    def _stochastic_k(self, highs, lows, closes, period):
        if len(closes) < period:
            return None
        highest = max(highs[-period:])
        lowest = min(lows[-period:])
        if highest == lowest:
            return 50.0
        return ((closes[-1] - lowest) / (highest - lowest)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        need_bars = self.period + 2
        closes = ctx.closes(need_bars)
        highs = ctx.highs(need_bars)
        lows = ctx.lows(need_bars)

        if len(closes) < need_bars:
            return None

        stoch_curr = self._stochastic_k(highs, lows, closes, self.period)
        stoch_prev = self._stochastic_k(highs[:-1], lows[:-1], closes[:-1], self.period)

        if stoch_curr is None or stoch_prev is None:
            return None

        # Manage open position exits at midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and stoch_curr >= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "stoch_midline_take_profit", "stoch": round(stoch_curr, 2)}
                )
            if direction == "short" and stoch_curr <= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "stoch_midline_take_profit", "stoch": round(stoch_curr, 2)}
                )
            return None

        # Mandatory hard cooldown after last exit to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long entry: extreme oversold bounce crossing back above lower threshold
        if stoch_prev <= self.lower_thresh and stoch_curr > self.lower_thresh:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=100.0,
                take_profit_bps=180.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "stoch_deep_oversold_cross_up",
                    "stoch_prev": round(stoch_prev, 2),
                    "stoch_curr": round(stoch_curr, 2)
                }
            )

        # Short entry: extreme overbought rejection crossing back below upper threshold
        if stoch_prev >= self.upper_thresh and stoch_curr < self.upper_thresh:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=100.0,
                take_profit_bps=180.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "stoch_deep_overbought_cross_down",
                    "stoch_prev": round(stoch_prev, 2),
                    "stoch_curr": round(stoch_curr, 2)
                }
            )

        return None