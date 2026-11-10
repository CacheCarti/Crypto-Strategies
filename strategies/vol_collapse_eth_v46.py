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
        "warmup_bars": 175,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.vol_window = 12
        self.breakout_period = 12
        self.spike_threshold_mult = 1.8
        
        self.had_spike = False
        self.is_compressed = False
        self.cooldown_remaining = 0
        self.bars_in_compression = 0

    def _realized_vol(self, closes, period):
        if len(closes) < period + 1:
            return 0.0
        log_rets = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            curr = closes[i]
            if prev > 0 and curr > 0:
                log_rets.append(math.log(curr / prev))
            else:
                log_rets.append(0.0)
        mean_ret = sum(log_rets) / len(log_rets)
        var = sum((r - mean_ret) ** 2 for r in log_rets) / len(log_rets)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + self.vol_window + 5)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)

        if len(closes) < self.median_period + self.vol_window + 1:
            return None

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        # Current short-term realized vol (12-bar window)
        current_vol = self._realized_vol(closes, self.vol_window)

        # Baseline: rolling median of realized volatility over ~168 bars
        # Sample every 4 bars across the 168-bar lookback for fast and robust median calculation
        sampled_vols = []
        step = 4
        start_idx = len(closes) - self.median_period
        for idx in range(start_idx, len(closes), step):
            sub_closes = closes[:idx]
            if len(sub_closes) >= self.vol_window + 1:
                sampled_vols.append(self._realized_vol(sub_closes, self.vol_window))

        if not sampled_vols:
            return None

        sampled_vols.sort()
        baseline_median = sampled_vols[len(sampled_vols) // 2]
        if baseline_median <= 1e-6:
            return None

        vol_ratio = current_vol / baseline_median

        # State transition: chaos spike -> compression coil
        if vol_ratio >= self.spike_threshold_mult:
            self.had_spike = True
            self.is_compressed = False
            self.bars_in_compression = 0
        elif self.had_spike and vol_ratio <= 1.0:
            self.is_compressed = True
            self.had_spike = False
            self.bars_in_compression = 0

        if self.is_compressed:
            self.bars_in_compression += 1
            if self.bars_in_compression > 36:
                # Coil expired without breaking out
                self.is_compressed = False

        if ctx.has_position():
            return None

        if self.cooldown_remaining > 0:
            return None

        # Coil release breakout check (using prior 12 completed bars)
        if self.is_compressed and len(highs) >= self.breakout_period + 1 and len(lows) >= self.breakout_period + 1:
            prev_high = max(highs[-(self.breakout_period + 1):-1])
            prev_low = min(lows[-(self.breakout_period + 1):-1])
            curr_close = closes[-1]

            # Long breakout on coil release
            if curr_close > prev_high:
                self.is_compressed = False
                self.cooldown_remaining = 8
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "vol_ratio": round(vol_ratio, 3),
                        "prev_high": round(prev_high, 2),
                        "close": round(curr_close, 2),
                        "bars_compressed": self.bars_in_compression,
                    }
                )

            # Short breakdown on coil release
            elif curr_close < prev_low:
                self.is_compressed = False
                self.cooldown_remaining = 8
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_short_breakout",
                        "vol_ratio": round(vol_ratio, 3),
                        "prev_low": round(prev_low, 2),
                        "close": round(curr_close, 2),
                        "bars_compressed": self.bars_in_compression,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_remaining = 8