from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 28
        self.lower_thresh = 8.0
        self.upper_thresh = 92.0
        self.cooldown_bars = 90
        self.last_exit_bar = -999

    def _calc_stoch(self, highs: list, lows: list, closes: list) -> Optional[float]:
        if len(closes) < self.period:
            return None
        highest = max(highs[-self.period:])
        lowest = min(lows[-self.period:])
        if highest == lowest:
            return 50.0
        return ((closes[-1] - lowest) / (highest - lowest)) * 100.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.period + 3
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        stoch_curr = self._calc_stoch(highs, lows, closes)
        stoch_prev = self._calc_stoch(highs[:-1], lows[:-1], closes[:-1])

        if stoch_curr is None or stoch_prev is None:
            return None

        # 1. Manage active position exit on midline reversion
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and stoch_curr >= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_midline_reached_long", "stoch": round(stoch_curr, 2)}
                )
            elif direction == "short" and stoch_curr <= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_midline_reached_short", "stoch": round(stoch_curr, 2)}
                )
            return None

        # 2. Strict cooldown to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. High-conviction mean reversion entries from deep extremes
        if stoch_prev <= self.lower_thresh and stoch_curr > 12.0:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=120.0,
                take_profit_bps=180.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "stoch_deep_oversold_snapback",
                    "stoch_curr": round(stoch_curr, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close,
                }
            )

        if stoch_prev >= self.upper_thresh and stoch_curr < 88.0:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=120.0,
                take_profit_bps=180.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "stoch_deep_overbought_snapback",
                    "stoch_curr": round(stoch_curr, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index