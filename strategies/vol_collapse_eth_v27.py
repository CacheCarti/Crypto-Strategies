from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilRelease(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Release ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # 12h average hold for structural swing
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.channel_period = 12
        self.cooldown_period = 8
        self.max_hold_bars = 20
        self.max_compression_window = 16

        self.last_exit_bar = -999
        self.spike_active = False
        self.compression_armed = False
        self.compression_start_bar = -999
        self.bars_in_pos = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.compression_armed = False
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 25)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.median_period + 20:
            return None

        # Position management & timed exit
        if ctx.has_position():
            self.bars_in_pos += 1
            if self.bars_in_pos >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "timed_structural_exit", "bars_held": self.bars_in_pos})
            return None
        else:
            self.bars_in_pos = 0

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Skip meltdown / extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            self.compression_armed = False
            self.spike_active = False
            return None

        # Realized volatility series over rolling 6 bars (1h returns)
        hist_len = self.median_period + 10
        recent_closes = closes[-hist_len:]
        
        # Calculate returns
        log_rets = []
        for i in range(1, len(recent_closes)):
            p0, p1 = recent_closes[i - 1], recent_closes[i]
            log_rets.append(abs((p1 - p0) / p0) if p0 > 0 else 0.0)

        rv_window = 6
        rv_series = []
        for i in range(rv_window, len(log_rets) + 1):
            chunk = log_rets[i - rv_window:i]
            rv_series.append(sum(chunk) / rv_window)

        if len(rv_series) < self.median_period:
            return None

        cur_rv = rv_series[-1]
        baseline_slice = sorted(rv_series[-self.median_period:])
        rv_median = baseline_slice[len(baseline_slice) // 2]

        if rv_median <= 1e-7:
            return None

        # State transition: chaos spike (>2x median) -> collapse back below median
        if cur_rv >= 2.0 * rv_median:
            self.spike_active = True
        elif self.spike_active and cur_rv < 1.0 * rv_median:
            self.spike_active = False
            self.compression_armed = True
            self.compression_start_bar = ctx.bar_index

        # Expire armed compression after fixed window
        if self.compression_armed and (ctx.bar_index - self.compression_start_bar > self.max_compression_window):
            self.compression_armed = False

        if not self.compression_armed:
            return None

        # Breakout of 12-bar coil channel (evaluating past 12 completed bars excluding current)
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        if len(prior_highs) < self.channel_period or len(prior_lows) < self.channel_period:
            return None

        coil_high = max(prior_highs)
        coil_low = min(prior_lows)
        curr_price = ctx.bar.close

        # Structural stops scaled to coil channel depth
        channel_height_bps = ((coil_high - coil_low) / curr_price) * 10000.0 if curr_price > 0 else 250.0
        stop_bps = max(180.0, min(360.0, channel_height_bps * 0.75))
        tp_bps = stop_bps * 2.0

        if curr_price > coil_high:
            self.compression_armed = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "coil_upside_release",
                    "cur_rv": round(cur_rv, 6),
                    "rv_median": round(rv_median, 6),
                    "coil_high": coil_high,
                    "channel_bps": round(channel_height_bps, 2),
                }
            )

        if curr_price < coil_low:
            self.compression_armed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "coil_downside_release",
                    "cur_rv": round(cur_rv, 6),
                    "rv_median": round(rv_median, 6),
                    "coil_low": coil_low,
                    "channel_bps": round(channel_height_bps, 2),
                }
            )

        return None