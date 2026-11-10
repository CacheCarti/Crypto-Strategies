from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilRelease(Strategy):
    METADATA = {
        "name": "PostSpikeCoilRelease",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.short_vol_window = 12
        self.channel_len = 12
        self.max_coil_age = 36
        self.cooldown_period = 14
        
        self.seen_spike = False
        self.spike_age = 0
        self.coiled = False
        self.coiled_age = 0
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.seen_spike = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = self.vol_window + self.short_vol_window + 5
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        # 1. Structural stop / ATR calculation for adaptive risk
        current_atr = self._atr(highs[-25:], lows[-25:], closes[-25:], 14)
        if current_atr is None or ctx.bar.close <= 0:
            return None
        atr_bps = (current_atr / ctx.bar.close) * 10000.0

        # Dynamic stops: wide enough to absorb giveback stop, capped safely
        stop_bps = max(180.0, min(380.0, atr_bps * 1.8))
        target_bps = stop_bps * 2.2

        # 2. Track Realized Volatility distribution (168-bar median of 12-bar ATR)
        recent_12_atr = []
        for offset in range(self.vol_window):
            end_idx = len(closes) - offset
            start_idx = max(0, end_idx - self.short_vol_window - 1)
            c_slice = closes[start_idx:end_idx]
            h_slice = highs[start_idx:end_idx]
            l_slice = lows[start_idx:end_idx]
            val = self._atr(h_slice, l_slice, c_slice, self.short_vol_window)
            if val is not None:
                recent_12_atr.append(val)

        if len(recent_12_atr) < self.vol_window:
            return None

        sorted_atr = sorted(recent_12_atr)
        median_atr = sorted_atr[len(sorted_atr) // 2]
        current_fast_atr = recent_12_atr[0]

        # 3. Detect Transition: Spike (>1.85x median) -> Collapse back below median
        if current_fast_atr >= median_atr * 1.85:
            self.seen_spike = True
            self.spike_age = 0
            self.coiled = False
        elif self.seen_spike:
            self.spike_age += 1
            if current_fast_atr < median_atr:
                self.coiled = True
                self.seen_spike = False
                self.coiled_age = 0
            elif self.spike_age > 48:
                self.seen_spike = False

        if self.coiled:
            self.coiled_age += 1
            if self.coiled_age > self.max_coil_age:
                self.coiled = False

        # 4. Entry check with strict cooldown & state management
        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        if not self.coiled:
            return None

        # Coil breakout of previous 12 bars (excluding current bar)
        recent_highs = highs[- (self.channel_len + 1): -1]
        recent_lows = lows[- (self.channel_len + 1): -1]
        breakout_high = max(recent_highs)
        breakout_low = min(recent_lows)

        # Confirm release with volume expansion over recent coil average
        vols = ctx.volumes(self.channel_len + 1)
        avg_vol = sum(vols[:-1]) / self.channel_len if len(vols) > self.channel_len else 1.0
        cur_vol = ctx.bar.volume
        vol_confirmed = cur_vol > avg_vol * 1.15

        if ctx.bar.close > breakout_high and vol_confirmed:
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.78,
                stop_loss_bps=stop_bps,
                take_profit_bps=target_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "coil_breakout_high_after_vol_collapse",
                    "fast_atr": round(current_fast_atr, 2),
                    "median_atr": round(median_atr, 2),
                    "breakout_high": round(breakout_high, 2),
                    "coiled_age": self.coiled_age,
                }
            )

        if ctx.bar.close < breakout_low and vol_confirmed:
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=target_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "coil_breakout_low_after_vol_collapse",
                    "fast_atr": round(current_fast_atr, 2),
                    "median_atr": round(median_atr, 2),
                    "breakout_low": round(breakout_low, 2),
                    "coiled_age": self.coiled_age,
                }
            )

        return None