from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.baseline_period = 144
        self.vol_window = 10
        self.channel_period = 14
        self.spike_multiplier = 1.85
        self.coil_multiplier = 1.10
        self.cooldown_bars = 6
        
        self.spike_memory_bars = 20
        self.spike_timer = 0
        self.last_entry_bar = -999
        self.last_exit_bar = -999

    def _stddev(self, values: list) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_period + self.vol_window + 5
        closes = ctx.closes(total_needed)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < total_needed or len(highs) < self.channel_period + 2:
            return None

        # Compute rolling percentage returns
        returns = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
            else:
                returns.append(0.0)

        if len(returns) < self.baseline_period + self.vol_window:
            return None

        # Calculate short-term realized volatility history
        vol_history = []
        for i in range(self.baseline_period):
            end_idx = len(returns) - (self.baseline_period - 1 - i)
            start_idx = end_idx - self.vol_window
            v = self._stddev(returns[start_idx:end_idx])
            vol_history.append(v)

        current_vol = vol_history[-1]
        sorted_vols = sorted(vol_history)
        baseline_vol = sorted_vols[len(sorted_vols) // 2]  # Rolling median vol

        if baseline_vol <= 0:
            return None

        # Track chaotic volatility spikes and transition to compression
        if current_vol >= baseline_vol * self.spike_multiplier:
            self.spike_timer = self.spike_memory_bars
        elif self.spike_timer > 0:
            self.spike_timer -= 1

        is_coiled = (self.spike_timer > 0) and (current_vol <= baseline_vol * self.coil_multiplier)

        # In-position management
        if ctx.has_position():
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_entry_bar < self.cooldown_bars) or \
           (ctx.bar_index - self.last_exit_bar < self.cooldown_bars):
            return None

        # Donchian channel boundaries of previous bars (exclude current forming bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]

        if not prior_highs or not prior_lows:
            return None

        channel_high = max(prior_highs)
        channel_low = min(prior_lows)
        current_close = ctx.bar.close

        # Coil Release: breakout from compression after a registered volatility spike
        if is_coiled and current_close > channel_high:
            self.spike_timer = 0  # Consume coil state on first release trigger
            self.last_entry_bar = ctx.bar_index
            vol_ratio = round(current_vol / baseline_vol, 3)
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_upside_breakout",
                    "current_vol": round(current_vol, 5),
                    "baseline_vol": round(baseline_vol, 5),
                    "vol_ratio": vol_ratio,
                    "channel_high": channel_high,
                    "close": current_close,
                },
            )

        if is_coiled and current_close < channel_low:
            self.spike_timer = 0  # Consume coil state on first release trigger
            self.last_entry_bar = ctx.bar_index
            vol_ratio = round(current_vol / baseline_vol, 3)
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_downside_breakout",
                    "current_vol": round(current_vol, 5),
                    "baseline_vol": round(baseline_vol, 5),
                    "vol_ratio": vol_ratio,
                    "channel_low": channel_low,
                    "close": current_close,
                },
            )

        return None