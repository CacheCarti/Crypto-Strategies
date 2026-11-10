from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_period = 12
        self.spike_threshold_mult = 1.8
        self.cooldown_period = 14
        self.max_coil_duration = 24

        self.last_exit_bar = -999
        self.spike_active = False
        self.coil_armed = False
        self.coil_start_bar = -999

    def _calc_parkinson_vol(self, highs, lows) -> Optional[float]:
        n = len(highs)
        if n < 2:
            return None
        sum_sq = 0.0
        for h, l in zip(highs, lows):
            if l <= 0:
                continue
            ratio = math.log(h / l)
            sum_sq += ratio * ratio
        return math.sqrt(sum_sq / (4.0 * math.log(2.0) * n))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_window + 15)
        highs = ctx.highs(self.vol_window + 15)
        lows = ctx.lows(self.vol_window + 15)

        if len(closes) < self.vol_window + 10:
            return None

        # Cooldown guard: enforce hard gap between consecutive trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Compute rolling short-term realized vol (8 bars) vs 168-bar median
        short_window = 8
        current_vol = self._calc_parkinson_vol(highs[-short_window:], lows[-short_window:])
        if current_vol is None or current_vol == 0:
            return None

        # Sample baseline distribution of short-term vols over 168 bars
        step = 4
        hist_vols = []
        for i in range(self.vol_window, short_window, -step):
            idx_end = len(highs) - i
            idx_start = idx_end - short_window
            if idx_start < 0:
                break
            v = self._calc_parkinson_vol(highs[idx_start:idx_end], lows[idx_start:idx_end])
            if v is not None:
                hist_vols.append(v)

        if len(hist_vols) < 15:
            return None

        hist_vols.sort()
        median_vol = hist_vols[len(hist_vols) // 2]
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol

        # Event state machine: Chaos -> Compression Coil -> Breakout Release
        if vol_ratio > self.spike_threshold_mult:
            self.spike_active = True
            self.coil_armed = False

        if self.spike_active and vol_ratio < 1.0:
            self.spike_active = False
            self.coil_armed = True
            self.coil_start_bar = ctx.bar_index

        # Expire coil if it stays compressed too long without breaking out
        if self.coil_armed and (ctx.bar_index - self.coil_start_bar > self.max_coil_duration):
            self.coil_armed = False

        if not self.coil_armed:
            return None

        # Only evaluate breakout entry if we do not already hold a position
        if ctx.has_position():
            return None

        current_close = ctx.bar.close
        # Prior 12-bar Donchian channel (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        breakout_high = max(prior_highs)
        breakout_low = min(prior_lows)

        # Ensure market is not in unhedged meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("MELTDOWN", "CRISIS"):
            return None

        if current_close > breakout_high:
            self.coil_armed = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_upper_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_level": round(breakout_high, 2),
                    "close": round(current_close, 2),
                    "regime": market_regime,
                },
            )

        if current_close < breakout_low:
            self.coil_armed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_lower_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_level": round(breakout_low, 2),
                    "close": round(current_close, 2),
                    "regime": market_regime,
                },
            )

        return None