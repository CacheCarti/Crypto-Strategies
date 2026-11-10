from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_window = 12
        self.baseline_window = 168
        self.breakout_window = 12
        self.cooldown_bars = 6
        self.last_exit_bar = -100

        self.state = "idle"  # "idle" -> "spiked" -> "compressed"
        self.state_bar = 0

    def _realized_vol(self, closes, window):
        if len(closes) < window + 1:
            return 0.0
        returns = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append(math.log(closes[i] / prev))
            else:
                returns.append(0.0)
        recent_ret = returns[-window:]
        mean = sum(recent_ret) / window
        var = sum((r - mean) ** 2 for r in recent_ret) / window
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "idle"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_window + self.rv_window + 5)
        if len(closes) < self.baseline_window + self.rv_window:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Measure 12-bar realized vol rolling history
        rv_series = []
        step = 4
        for offset in range(0, self.baseline_window, step):
            idx = len(closes) - offset
            sub_closes = closes[:idx]
            if len(sub_closes) >= self.rv_window + 1:
                rv_series.append(self._realized_vol(sub_closes, self.rv_window))

        if not rv_series:
            return None

        current_rv = rv_series[0]
        sorted_rv = sorted(rv_series)
        n = len(sorted_rv)
        p30 = sorted_rv[int(0.30 * n)]
        p80 = sorted_rv[int(0.80 * n)]

        # State transition: chaos -> compression
        if self.state == "idle":
            if current_rv >= p80:
                self.state = "spiked"
                self.state_bar = ctx.bar_index
        elif self.state == "spiked":
            if current_rv <= p30:
                self.state = "compressed"
                self.state_bar = ctx.bar_index
            elif ctx.bar_index - self.state_bar > 48:
                self.state = "idle"
        elif self.state == "compressed":
            if ctx.bar_index - self.state_bar > 36:
                self.state = "idle"

        if ctx.has_position():
            # Check early exit if compression dissolved back into heavy chaos
            if current_rv > p80:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "vol_re_expanded_exit", "current_rv": current_rv, "p80": p80}
                )
            return None

        # Entry logic: only when in compressed state following a spike
        if self.state == "compressed":
            highs = ctx.highs(self.breakout_window + 1)
            lows = ctx.lows(self.breakout_window + 1)
            if len(highs) < self.breakout_window + 1 or len(lows) < self.breakout_window + 1:
                return None

            donchian_high = max(highs[-self.breakout_window - 1 : -1])
            donchian_low = min(lows[-self.breakout_window - 1 : -1])
            curr_close = ctx.bar.close

            # Release of the coil
            if curr_close > donchian_high:
                self.state = "idle"
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "coil_release_upbreak",
                        "current_rv": current_rv,
                        "p30": p30,
                        "p80": p80,
                        "breakout_level": donchian_high,
                        "close": curr_close
                    }
                )
            elif curr_close < donchian_low:
                self.state = "idle"
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "coil_release_downbreak",
                        "current_rv": current_rv,
                        "p30": p30,
                        "p80": p80,
                        "breakout_level": donchian_low,
                        "close": curr_close
                    }
                )

        return None