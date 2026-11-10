from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_short_period = 12
        self.rv_long_period = 168
        self.breakout_period = 12
        self.spike_threshold = 1.8
        self.cooldown_period = 8
        self.spike_detected = False
        self.coil_active = False
        self.last_exit_bar = -999

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            r = math.log(closes[i] / closes[i - 1])
            returns.append(r)
        mean_r = sum(returns) / period
        var = sum((r - mean_r) ** 2 for r in returns) / period
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_active = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rv_long_period + self.rv_short_period + 5)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)

        if len(closes) < self.rv_long_period + self.rv_short_period:
            return None

        current_rv = self._realized_vol(closes, self.rv_short_period)
        if current_rv is None:
            return None

        rv_history = []
        step = 6
        for offset in range(0, self.rv_long_period - self.rv_short_period, step):
            sub_closes = closes[-(self.rv_short_period + 1 + offset) : len(closes) - offset]
            rv_val = self._realized_vol(sub_closes, self.rv_short_period)
            if rv_val is not None:
                rv_history.append(rv_val)

        if not rv_history:
            return None

        rv_history.sort()
        median_rv = rv_history[len(rv_history) // 2]
        if median_rv <= 0.0:
            return None

        if current_rv >= median_rv * self.spike_threshold:
            self.spike_detected = True

        if self.spike_detected and (current_rv <= median_rv * 1.05):
            self.coil_active = True
            self.spike_detected = False

        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        if not self.coil_active:
            return None

        recent_high = max(highs[- (self.breakout_period + 1) : -1])
        recent_low = min(lows[- (self.breakout_period + 1) : -1])
        price = ctx.bar.close

        ratio = current_rv / median_rv
        confidence = max(0.4, min(0.9, 0.5 + (1.0 - ratio) * 0.4))

        if price > recent_high:
            self.coil_active = False
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_breakout_high",
                    "price": price,
                    "breakout_high": recent_high,
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "rv_ratio": ratio,
                },
            )

        if price < recent_low:
            self.coil_active = False
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_breakdown_low",
                    "price": price,
                    "breakdown_low": recent_low,
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "rv_ratio": ratio,
                },
            )

        return None