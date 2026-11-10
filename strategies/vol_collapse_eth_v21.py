from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilStrategy(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # 5 hours typical hold
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.med_period = 168
        self.vol_short_period = 12
        self.channel_period = 12
        self.cooldown_bars = 6
        self.max_hold_bars = 16

        self.last_exit_bar = -100
        self.entry_bar = -100
        self.spike_observed = False
        self.coiled = False
        self.coil_bars_count = 0

    def _realized_vol(self, closes, period):
        if len(closes) < period + 1:
            return 0.0
        ret_sq = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            r = (closes[i] - prev) / prev
            ret_sq.append(r * r)
        if not ret_sq:
            return 0.0
        return math.sqrt(sum(ret_sq) / len(ret_sq))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100
        # Reset coil state upon exit to force fresh sequence
        self.coiled = False
        self.spike_observed = False
        self.coil_bars_count = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.med_period + 20)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.med_period + 14 or len(highs) < self.channel_period + 2:
            return None

        # Position management: time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_coil_exhaustion", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate current short-term realized volatility (12 bars)
        curr_vol = self._realized_vol(closes, self.vol_short_period)

        # Calculate rolling baseline: sample 12-bar RV over last 168 bars every 6 bars for speed
        vol_samples = []
        for idx in range(12, len(closes), 6):
            sub = closes[:idx]
            v = self._realized_vol(sub, self.vol_short_period)
            if v > 0:
                vol_samples.append(v)

        if len(vol_samples) < 10:
            return None

        sorted_vols = sorted(vol_samples)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        if median_vol <= 1e-7:
            return None

        # Tracking state machine: Chaos -> Compression (Coil)
        if curr_vol >= 1.9 * median_vol:
            self.spike_observed = True
            self.coiled = False
            self.coil_bars_count = 0
        elif self.spike_observed and curr_vol <= 1.05 * median_vol:
            self.coiled = True
            self.spike_observed = False
            self.coil_bars_count = 0

        if self.coiled:
            self.coil_bars_count += 1
            # Coil window valid for up to 36 bars
            if self.coil_bars_count > 36:
                self.coiled = False
                return None

            # Channel breakout trigger (prior 12 bars excluding current bar)
            prior_highs = highs[-(self.channel_period + 1):-1]
            prior_lows = lows[-(self.channel_period + 1):-1]
            break_high = max(prior_highs)
            break_low = min(prior_lows)

            current_close = ctx.bar.close

            # Trend bias filter using 48-period drift
            drift = (closes[-1] - closes[-48]) / closes[-48] if len(closes) >= 48 else 0.0

            # Coil release long
            if current_close > break_high and drift >= -0.015:
                self.entry_bar = ctx.bar_index
                self.coiled = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_upside_break",
                        "curr_vol": curr_vol,
                        "median_vol": median_vol,
                        "break_high": break_high,
                        "close": current_close
                    }
                )

            # Coil release short
            if current_close < break_low and drift <= 0.015:
                self.entry_bar = ctx.bar_index
                self.coiled = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_release_downside_break",
                        "curr_vol": curr_vol,
                        "median_vol": median_vol,
                        "break_low": break_low,
                        "close": current_close
                    }
                )

        return None