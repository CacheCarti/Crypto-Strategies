from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_short_window = 10
        self.vol_baseline_window = 168
        self.spike_mult = 2.10
        self.compression_mult = 0.92
        self.channel_period = 16
        self.cooldown_bars = 24
        self.max_coil_wait_bars = 28
        self.min_coil_bars = 3

        self.spike_detected = False
        self.coiled = False
        self.bars_since_spike = 0
        self.bars_coiled = 0
        self.last_trade_bar = -999

    def _calc_vol_history(self, closes: list) -> list:
        req_len = self.vol_baseline_window + self.vol_short_window
        if len(closes) < req_len + 1:
            return []

        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        vols = []
        for i in range(len(returns) - self.vol_baseline_window, len(returns) + 1):
            window = returns[i - self.vol_short_window:i]
            if len(window) < self.vol_short_window:
                continue
            mean = sum(window) / self.vol_short_window
            var = sum((x - mean) ** 2 for x in window) / self.vol_short_window
            vols.append(math.sqrt(var))
        return vols

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.spike_detected = False
        self.coiled = False
        self.bars_since_spike = 0
        self.bars_coiled = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.vol_baseline_window + self.vol_short_window + self.channel_period + 10
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)
        volumes = ctx.volumes(self.channel_period + 5)

        if len(closes) < needed_bars or len(highs) < self.channel_period + 2 or len(volumes) < self.channel_period + 5:
            return None

        # Manage lifecycle timers
        if self.spike_detected:
            self.bars_since_spike += 1
            if self.coiled:
                self.bars_coiled += 1

            if self.bars_since_spike > self.max_coil_wait_bars:
                self.spike_detected = False
                self.coiled = False
                self.bars_since_spike = 0
                self.bars_coiled = 0

        # Calculate realized volatility vs 168-bar median baseline
        vols = self._calc_vol_history(closes)
        if len(vols) < self.vol_baseline_window:
            return None

        current_vol = vols[-1]
        baseline_median_vol = statistics.median(vols[-self.vol_baseline_window:])
        if baseline_median_vol <= 0:
            return None

        vol_ratio = current_vol / baseline_median_vol

        # Regime Phase 1: Realized volatility spikes above threshold
        if vol_ratio >= self.spike_mult:
            self.spike_detected = True
            self.coiled = False
            self.bars_since_spike = 0
            self.bars_coiled = 0

        # Regime Phase 2: Volatility collapses back into compression below baseline
        elif self.spike_detected and vol_ratio <= self.compression_mult:
            self.coiled = True

        # Existing position or mandatory cooldown guard
        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: Skip crisis / meltdown states
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Regime Phase 3: Trade breakout upon confirmed coil compression
        if self.coiled and self.bars_coiled >= self.min_coil_bars:
            breakout_high = max(highs[-self.channel_period - 1:-1])
            breakout_low = min(lows[-self.channel_period - 1:-1])
            curr_close = closes[-1]
            avg_vol = sum(volumes[-self.channel_period - 1:-1]) / self.channel_period
            curr_vol_bar = volumes[-1]

            # Volume expansion confirmation (must exceed 1.15x average)
            vol_confirmed = curr_vol_bar > (avg_vol * 1.15) if avg_vol > 0 else True
            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Long breakout on coil release
            if curr_close > breakout_high and vol_confirmed and trend_regime != "bear":
                self.spike_detected = False
                self.coiled = False
                self.bars_since_spike = 0
                self.bars_coiled = 0
                self.last_trade_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=0.82,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "vol_ratio": round(vol_ratio, 3),
                        "curr_vol": round(current_vol, 6),
                        "median_vol": round(baseline_median_vol, 6),
                        "breakout_high": round(breakout_high, 2),
                        "close": round(curr_close, 2),
                        "bars_coiled": self.bars_coiled,
                        "trend_regime": trend_regime
                    }
                )

            # Short breakdown on coil release
            elif curr_close < breakout_low and vol_confirmed and trend_regime != "bull":
                self.spike_detected = False
                self.coiled = False
                self.bars_since_spike = 0
                self.bars_coiled = 0
                self.last_trade_bar = ctx.bar_index

                return ctx.signal(
                    "short",
                    confidence=0.78,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_short_breakout",
                        "vol_ratio": round(vol_ratio, 3),
                        "curr_vol": round(current_vol, 6),
                        "median_vol": round(baseline_median_vol, 6),
                        "breakout_low": round(breakout_low, 2),
                        "close": round(curr_close, 2),
                        "bars_coiled": self.bars_coiled,
                        "trend_regime": trend_regime
                    }
                )

        return None