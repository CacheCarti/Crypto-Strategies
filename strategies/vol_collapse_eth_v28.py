from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 12
        self.median_period = 168
        self.channel_period = 12
        self.max_compression_window = 36
        self.spike_threshold_mult = 1.75
        
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.state = "NORMAL"
        self.state_bar = 0

    def _realized_vol(self, closes: list, window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        rets = []
        for i in range(len(closes) - window, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            rets.append(math.log(closes[i] / prev))
        if not rets:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "NORMAL"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + self.rv_period + 5)
        if len(closes) < self.median_period + self.rv_period:
            return None

        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)
        if len(highs) < self.channel_period + 2 or len(lows) < self.channel_period + 2:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate current short-term realized volatility (12-bar window)
        current_rv = self._realized_vol(closes, self.rv_period)
        if current_rv is None:
            return None

        # Calculate historical distribution of 12-bar RV over the last 168 bars
        rv_hist = []
        step = 4  # sample every 4 bars for performance across long history
        for idx in range(len(closes) - self.median_period, len(closes) + 1, step):
            sub_closes = closes[:idx]
            sub_rv = self._realized_vol(sub_closes, self.rv_period)
            if sub_rv is not None:
                rv_hist.append(sub_rv)

        if len(rv_hist) < 10:
            return None

        sorted_rv = sorted(rv_hist)
        median_rv = sorted_rv[len(sorted_rv) // 2]
        if median_rv <= 0.0:
            return None

        # State transition machine: NORMAL -> SPIKE -> COIL -> RELEASE (Trade)
        if self.state == "NORMAL":
            if current_rv >= median_rv * self.spike_threshold_mult:
                self.state = "SPIKE"
                self.state_bar = ctx.bar_index

        elif self.state == "SPIKE":
            if current_rv <= median_rv * 1.05:
                self.state = "COIL"
                self.state_bar = ctx.bar_index
            elif ctx.bar_index - self.state_bar > 48:
                self.state = "NORMAL"

        elif self.state == "COIL":
            if ctx.bar_index - self.state_bar > self.max_compression_window:
                self.state = "NORMAL"
                return None

            if current_rv > median_rv * 1.5:
                self.state = "NORMAL"
                return None

            prev_high = max(highs[-self.channel_period - 1:-1])
            prev_low = min(lows[-self.channel_period - 1:-1])
            close_price = ctx.bar.close

            # Breakout trigger on channel release
            if close_price > prev_high:
                self.state = "NORMAL"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "close": close_price,
                        "prev_high": prev_high,
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "bars_in_coil": ctx.bar_index - self.state_bar,
                    }
                )
            elif close_price < prev_low:
                self.state = "NORMAL"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "post_spike_coil_short_breakout",
                        "close": close_price,
                        "prev_low": prev_low,
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "bars_in_coil": ctx.bar_index - self.state_bar,
                    }
                )

        return None