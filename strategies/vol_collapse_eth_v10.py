from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilRelease(Strategy):
    METADATA = {
        "name": "PostSpikeCoilRelease",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_len = 12
        self.cooldown_period = 16
        self.cooldown_until = 0

        self.spike_active = False
        self.spike_cooldown_bars = 0
        self.coil_armed = False
        self.coil_bars_remaining = 0
        self.max_coil_duration = 24

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_period
        self.coil_armed = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_window + 15)
        highs = ctx.highs(self.channel_len + 2)
        lows = ctx.lows(self.channel_len + 2)

        if len(closes) < self.vol_window + 14:
            return None

        # Compute rolling 14-bar Realized Volatility over the last vol_window
        # Use log return standard deviation approximation
        returns = []
        for i in range(1, len(closes)):
            returns.append(math.log(closes[i] / closes[i - 1]))

        # Calculate rv series of 14-bar window
        rv_series = []
        for i in range(14, len(returns) + 1):
            sub = returns[i - 14:i]
            mean_r = sum(sub) / 14.0
            var_r = sum((x - mean_r) ** 2 for x in sub) / 14.0
            rv_series.append(math.sqrt(var_r))

        if len(rv_series) < self.vol_window:
            return None

        recent_rv = rv_series[-1]
        baseline_slice = sorted(rv_series[-self.vol_window:])
        median_rv = baseline_slice[len(baseline_slice) // 2]

        if median_rv <= 1e-8:
            return None

        # State 1: Detect high-vol chaos event
        if recent_rv > 2.1 * median_rv:
            self.spike_active = True
            self.spike_cooldown_bars = 0

        # State 2: Detect transition back below baseline (collapse into compression)
        if self.spike_active:
            self.spike_cooldown_bars += 1
            if recent_rv < 1.0 * median_rv:
                # Compression transition confirmed: arm the coil trigger
                self.spike_active = False
                self.coil_armed = True
                self.coil_bars_remaining = self.max_coil_duration

        if self.coil_armed:
            self.coil_bars_remaining -= 1
            if self.coil_bars_remaining <= 0:
                self.coil_armed = False

        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until:
            return None

        # Entry Trigger: First break of channel while coil is armed
        if self.coil_armed and len(highs) >= self.channel_len + 2:
            prev_high = max(highs[-self.channel_len - 1:-1])
            prev_low = min(lows[-self.channel_len - 1:-1])
            curr_close = ctx.bar.close

            if curr_close > prev_high:
                self.coil_armed = False
                self.cooldown_until = ctx.bar_index + self.cooldown_period
                return ctx.signal(
                    "long",
                    confidence=0.80,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_upside_breakout",
                        "rv_ratio": round(recent_rv / median_rv, 3),
                        "prev_high": round(prev_high, 2),
                        "price": round(curr_close, 2)
                    }
                )
            elif curr_close < prev_low:
                self.coil_armed = False
                self.cooldown_until = ctx.bar_index + self.cooldown_period
                return ctx.signal(
                    "short",
                    confidence=0.80,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_downside_breakout",
                        "rv_ratio": round(recent_rv / median_rv, 3),
                        "prev_low": round(prev_low, 2),
                        "price": round(curr_close, 2)
                    }
                )

        return None