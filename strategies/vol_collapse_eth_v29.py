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
        self.rv_period = 12
        self.baseline_period = 168
        self.breakout_period = 12
        self.cooldown_bars = 8
        self.state = "IDLE"  # IDLE -> SPIKED -> COILING
        self.bars_in_state = 0
        self.max_coil_window = 24
        self.last_exit_bar = -999

    def _median(self, values):
        s = sorted(values)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 != 0 else (s[mid - 1] + s[mid]) / 2.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"
        self.bars_in_state = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_period + self.rv_period + 5)
        if len(closes) < self.baseline_period + self.rv_period:
            return None

        # Compute rolling realized volatility (standard deviation of log returns)
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(log_rets) < self.baseline_period + self.rv_period:
            return None

        rv_series = []
        for i in range(self.baseline_period + 1):
            window = log_rets[i : i + self.rv_period]
            mean_r = sum(window) / self.rv_period
            var_r = sum((r - mean_r) ** 2 for r in window) / self.rv_period
            rv_series.append(math.sqrt(var_r))

        current_rv = rv_series[-1]
        baseline_median_rv = self._median(rv_series[:-1])
        if baseline_median_rv <= 1e-8:
            return None

        rv_ratio = current_rv / baseline_median_rv

        # State machine for Spike -> Compression (Coil)
        if self.state == "IDLE":
            if rv_ratio >= 1.85:
                self.state = "SPIKED"
                self.bars_in_state = 0
        elif self.state == "SPIKED":
            self.bars_in_state += 1
            if rv_ratio < 1.0:
                self.state = "COILING"
                self.bars_in_state = 0
            elif self.bars_in_state > 36:
                self.state = "IDLE"
        elif self.state == "COILING":
            self.bars_in_state += 1
            if self.bars_in_state > self.max_coil_window:
                self.state = "IDLE"

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Coil release entry: look for Donchian breakout during COILING state
        if self.state == "COILING":
            highs = ctx.highs(self.breakout_period + 2)
            lows = ctx.lows(self.breakout_period + 2)
            if len(highs) < self.breakout_period + 2:
                return None

            donchian_high = max(highs[-self.breakout_period - 1 : -1])
            donchian_low = min(lows[-self.breakout_period - 1 : -1])
            curr_close = ctx.bar.close

            # Filter against hostile macro crises
            crisis_score = ctx.market.get("crisis_score", 0.0)
            if crisis_score > 0.6:
                return None

            if curr_close > donchian_high:
                self.state = "IDLE"
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_upside_breakout",
                        "rv_ratio": round(rv_ratio, 3),
                        "donchian_high": round(donchian_high, 2),
                        "close": round(curr_close, 2),
                        "bars_coiling": self.bars_in_state,
                    },
                )
            elif curr_close < donchian_low:
                self.state = "IDLE"
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_downside_breakout",
                        "rv_ratio": round(rv_ratio, 3),
                        "donchian_low": round(donchian_low, 2),
                        "close": round(curr_close, 2),
                        "bars_coiling": self.bars_in_state,
                    },
                )

        return None