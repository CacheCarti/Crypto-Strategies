from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 3600 * 10,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 14
        self.median_period = 168
        self.breakout_period = 12
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spike_detected = False
        self.coiling = False
        self.bars_since_coil = 0
        self.max_coil_wait = 24

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiling = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.median_period + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # 1. Compute rolling ATR series to calculate current ATR and median ATR over 168 bars
        atr_series = []
        # Calculate ATRs across the lookback
        step = 4  # sample every 4 bars across 168 bars to keep computation fast
        for idx in range(len(closes) - self.median_period, len(closes) + 1, step):
            sub_c = closes[:idx]
            sub_h = highs[:idx]
            sub_l = lows[:idx]
            val = self._atr(sub_h, sub_l, sub_c, self.vol_period)
            if val is not None:
                atr_series.append(val)

        if len(atr_series) < 10:
            return None

        current_atr = atr_series[-1]
        sorted_atrs = sorted(atr_series)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]

        if median_atr <= 0:
            return None

        vol_ratio = current_atr / median_atr

        # Track spike and collapse state machine
        if vol_ratio > 1.8:
            self.spike_detected = True

        if self.spike_detected and vol_ratio <= 1.05:
            self.coiling = True
            self.spike_detected = False
            self.bars_since_coil = 0

        if self.coiling:
            self.bars_since_coil += 1
            if self.bars_since_coil > self.max_coil_wait:
                self.coiling = False

        if ctx.has_position():
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check breakout only when in coiling regime
        if self.coiling:
            # 12-bar Donchian breakout (excluding current bar)
            prior_highs = highs[-self.breakout_period - 1:-1]
            prior_lows = lows[-self.breakout_period - 1:-1]
            breakout_high = max(prior_highs)
            breakout_low = min(prior_lows)

            current_close = closes[-1]
            
            # Trend regime filter from market context
            trend_regime = ctx.market.get("trend_regime", "neutral")

            if current_close > breakout_high and trend_regime != "bear":
                self.coiling = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_upside_breakout",
                        "current_atr": round(current_atr, 2),
                        "median_atr": round(median_atr, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "breakout_high": round(breakout_high, 2),
                        "close": round(current_close, 2)
                    }
                )

            elif current_close < breakout_low and trend_regime != "bull":
                self.coiling = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_downside_breakout",
                        "current_atr": round(current_atr, 2),
                        "median_atr": round(median_atr, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "breakout_low": round(breakout_low, 2),
                        "close": round(current_close, 2)
                    }
                )

        return None