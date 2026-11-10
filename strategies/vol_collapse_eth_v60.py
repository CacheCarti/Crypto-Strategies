from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_vol = 168
        self.channel_len = 12
        self.cooldown_period = 8
        self.cooldown_until = 0
        
        self.spike_detected = False
        self.coil_armed = False
        self.armed_bar = 0
        self.max_armed_window = 24

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_period
        self.coil_armed = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback_vol + 15
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(self.channel_len + 2)
        lows = ctx.lows(self.channel_len + 2)
        if len(highs) < self.channel_len + 2 or len(lows) < self.channel_len + 2:
            return None

        # Manage cooldown
        if ctx.bar_index < self.cooldown_until:
            return None

        if ctx.has_position():
            return None

        # Compute rolling 10-bar realized volatility over the last 168 bars
        # rv_t = sqrt(sum of squared log returns over 10 bars)
        rv_series = []
        for i in range(10, len(closes)):
            chunk = closes[i-10:i+1]
            sq_sum = 0.0
            for j in range(1, len(chunk)):
                r = math.log(chunk[j] / chunk[j-1])
                sq_sum += r * r
            rv_series.append(math.sqrt(sq_sum / 10.0))

        if len(rv_series) < self.lookback_vol:
            return None

        curr_rv = rv_series[-1]
        prev_rv = rv_series[-2]
        window_rv = sorted(rv_series[-self.lookback_vol:])
        median_rv = window_rv[len(window_rv) // 2]

        if median_rv <= 0:
            return None

        # State transition 1: Volatility spikes above 2.0x median
        if curr_rv >= 2.0 * median_rv:
            self.spike_detected = True

        # State transition 2: Following a spike, RV collapses back below median -> coil is armed
        if self.spike_detected and prev_rv >= median_rv and curr_rv < median_rv:
            self.spike_detected = False
            self.coil_armed = True
            self.armed_bar = ctx.bar_index

        # Expire armed coil if no breakout happens within max_armed_window
        if self.coil_armed and (ctx.bar_index - self.armed_bar > self.max_armed_window):
            self.coil_armed = False

        # If coil is active, look for structural breakout release
        if self.coil_armed:
            prev_channel_highs = highs[-(self.channel_len + 1):-1]
            prev_channel_lows = lows[-(self.channel_len + 1):-1]
            highest_high = max(prev_channel_highs)
            lowest_low = min(prev_channel_lows)

            curr_close = ctx.bar.close

            if curr_close > highest_high:
                self.coil_armed = False
                self.cooldown_until = ctx.bar_index + self.cooldown_period
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_upside_breakout",
                        "curr_rv": curr_rv,
                        "median_rv": median_rv,
                        "highest_high": highest_high,
                        "price": curr_close,
                    }
                )

            elif curr_close < lowest_low:
                self.coil_armed = False
                self.cooldown_until = ctx.bar_index + self.cooldown_period
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_downside_breakout",
                        "curr_rv": curr_rv,
                        "median_rv": median_rv,
                        "lowest_low": lowest_low,
                        "price": curr_close,
                    }
                )

        return None