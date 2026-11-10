from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.baseline_period = 168
        self.short_vol_period = 14
        self.channel_period = 12
        self.spike_threshold = 1.8
        self.cooldown_bars = 6
        self.max_hold_bars = 16

        self.had_spike = False
        self.coiled = False
        self.bars_since_coil = 0
        self.bars_since_exit = 10
        self.entry_bar = 0

    def _realized_vol(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - period, len(closes))]
        mean = sum(returns) / period
        variance = sum((r - mean) ** 2 for r in returns) / (period - 1) if period > 1 else 0.0
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0
        self.had_spike = False
        self.coiled = False
        self.bars_since_coil = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1

        closes = ctx.closes(self.baseline_period + 10)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.baseline_period + 2 or len(highs) < self.channel_period + 2:
            return None

        # Position management: time-based holding exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_exit", "bars_held": bars_held})
            return None

        # Cooldown guard
        if self.bars_since_exit < self.cooldown_bars:
            return None

        # Calculate current short-term volatility
        rv_short = self._realized_vol(closes, self.short_vol_period)
        if rv_short is None or rv_short == 0:
            return None

        # Calculate baseline median volatility across trailing baseline_period
        step = 4
        sample_vols = []
        for offset in range(0, self.baseline_period - self.short_vol_period, step):
            end_idx = len(closes) - offset
            sub_closes = closes[:end_idx]
            vol_sample = self._realized_vol(sub_closes, self.short_vol_period)
            if vol_sample is not None:
                sample_vols.append(vol_sample)

        if len(sample_vols) < 10:
            return None

        sample_vols.sort()
        rv_median = sample_vols[len(sample_vols) // 2]
        if rv_median == 0:
            return None

        # Track Spike -> Coil state machine
        if rv_short >= rv_median * self.spike_threshold:
            self.had_spike = True
            self.coiled = False
            self.bars_since_coil = 0
        elif self.had_spike and rv_short <= rv_median:
            self.coiled = True
            self.had_spike = False
            self.bars_since_coil = 0

        if self.coiled:
            self.bars_since_coil += 1
            # Coil setup expires after 18 bars without breakout
            if self.bars_since_coil > 18:
                self.coiled = False
                return None

            # Donchian-style breakout of prior 12 bars (excluding current bar)
            prior_highs = highs[-(self.channel_period + 1):-1]
            prior_lows = lows[-(self.channel_period + 1):-1]
            breakout_high = max(prior_highs)
            breakout_low = min(prior_lows)

            current_close = ctx.bar.close
            vol_ratio = round(rv_short / rv_median, 3)

            if current_close > breakout_high:
                self.coiled = False
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    metadata={
                        "reason": "post_spike_coil_bull_breakout",
                        "vol_ratio": vol_ratio,
                        "breakout_high": breakout_high,
                        "close": current_close,
                        "bars_since_coil": self.bars_since_coil,
                    },
                )
            elif current_close < breakout_low:
                self.coiled = False
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    metadata={
                        "reason": "post_spike_coil_bear_breakout",
                        "vol_ratio": vol_ratio,
                        "breakout_low": breakout_low,
                        "close": current_close,
                        "bars_since_coil": self.bars_since_coil,
                    },
                )

        return None