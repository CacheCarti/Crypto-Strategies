from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_vol = 168
        self.atr_period = 14
        self.donchian_period = 12
        self.spike_threshold = 1.75
        
        self.state = "IDLE"  # "IDLE", "SPIKED", "COILED"
        self.coiled_bars = 0
        self.max_coil_window = 16
        self.last_exit_bar = -999
        self.cooldown_bars = 6

    def _atr(self, highs, lows, closes, period):
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"
        self.coiled_bars = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_needed = self.lookback_vol + self.atr_period + 2
        closes = ctx.closes(n_needed)
        if len(closes) < n_needed:
            return None

        highs = ctx.highs(n_needed)
        lows = ctx.lows(n_needed)

        # Precompute rolling ATRs to evaluate volatility spike & collapse
        # Compute normalized ATR (ATR / Close) for relative volatility
        curr_atr = self._atr(highs, lows, closes, self.atr_period)
        if curr_atr is None or closes[-1] == 0:
            return None
        curr_natr = curr_atr / closes[-1]

        # Sample historical natr over lookback to compute baseline median
        # Step every 4 bars to keep per-bar compute extremely lean
        natr_samples = []
        for offset in range(0, self.lookback_vol, 4):
            end_idx = len(closes) - offset
            start_idx = end_idx - (self.atr_period + 1)
            sub_h = highs[start_idx:end_idx]
            sub_l = lows[start_idx:end_idx]
            sub_c = closes[start_idx:end_idx]
            a = self._atr(sub_h, sub_l, sub_c, self.atr_period)
            if a is not None and sub_c[-1] > 0:
                natr_samples.append(a / sub_c[-1])

        if len(natr_samples) < 10:
            return None

        natr_samples.sort()
        baseline_med = natr_samples[len(natr_samples) // 2]
        if baseline_med == 0:
            return None

        vol_ratio = curr_natr / baseline_med

        # State machine transition
        if self.state == "IDLE":
            if vol_ratio >= self.spike_threshold:
                self.state = "SPIKED"
        elif self.state == "SPIKED":
            # Realized volatility collapsed back below median -> Coiled state activated
            if vol_ratio < 1.05:
                self.state = "COILED"
                self.coiled_bars = 0
        elif self.state == "COILED":
            self.coiled_bars += 1
            if self.coiled_bars > self.max_coil_window:
                self.state = "IDLE"

        # Cooldown & position guard
        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Look for breakout only during active coil regime
        if self.state == "COILED":
            # Exclude current bar when computing channel extremes
            channel_high = max(highs[-(self.donchian_period + 1):-1])
            channel_low = min(lows[-(self.donchian_period + 1):-1])
            curr_c = closes[-1]

            # Donchian expansion triggers
            if curr_c > channel_high:
                self.state = "IDLE"
                conf = min(0.85, 0.55 + 0.15 * max(0.0, 1.0 - (vol_ratio / 1.05)))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    metadata={
                        "reason": "coil_breakout_long",
                        "vol_ratio": round(vol_ratio, 3),
                        "coiled_bars": self.coiled_bars,
                        "channel_high": round(channel_high, 2),
                        "close": round(curr_c, 2)
                    }
                )
            elif curr_c < channel_low:
                self.state = "IDLE"
                conf = min(0.85, 0.55 + 0.15 * max(0.0, 1.0 - (vol_ratio / 1.05)))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    metadata={
                        "reason": "coil_breakout_short",
                        "vol_ratio": round(vol_ratio, 3),
                        "coiled_bars": self.coiled_bars,
                        "channel_low": round(channel_low, 2),
                        "close": round(curr_c, 2)
                    }
                )

        return None