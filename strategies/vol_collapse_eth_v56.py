from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilBreakout(Strategy):
    METADATA = {
        "name": "EthVolCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_lookback = 168
        self.short_vol_len = 8
        self.breakout_len = 12
        self.cooldown_period = 16
        
        # State tracking
        self.spike_observed = False
        self.bars_since_spike = 0
        self.max_wait_after_spike = 30
        self.last_exit_bar = -100

    def _calc_vol(self, closes: list, window: int) -> float:
        if len(closes) < window + 1:
            return 0.0
        rets = [abs(closes[i] / closes[i - 1] - 1.0) for i in range(len(closes) - window, len(closes))]
        return sum(rets) / window

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.spike_observed = False
        self.bars_since_spike = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_lookback + 10)
        if len(closes) < self.vol_lookback + 2:
            return None

        # Cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Calculate realized volatility over rolling windows
        short_vol = self._calc_vol(closes, self.short_vol_len)
        
        # Rolling baseline volatility (sampled every 4 bars across lookback to preserve CPU)
        sampled_vols = []
        for i in range(self.short_vol_len, self.vol_lookback, 4):
            sub = closes[:-i]
            if len(sub) > self.short_vol_len:
                sampled_vols.append(self._calc_vol(sub, self.short_vol_len))

        if not sampled_vols:
            return None

        sampled_vols.sort()
        med_vol = sampled_vols[len(sampled_vols) // 2]
        if med_vol <= 1e-7:
            return None

        # Track spike and compression cycle
        if short_vol > 1.9 * med_vol:
            self.spike_observed = True
            self.bars_since_spike = 0
        elif self.spike_observed:
            self.bars_since_spike += 1
            if self.bars_since_spike > self.max_wait_after_spike:
                self.spike_observed = False
                self.bars_since_spike = 0

        # Compression state: previously spiked, now returned to/below median vol
        is_compressed = self.spike_observed and (short_vol <= 1.05 * med_vol) and (self.bars_since_spike >= 2)

        # In-position checks
        if ctx.has_position():
            return None

        if not is_compressed:
            return None

        # Donchian channel breakout on compression
        highs = ctx.highs(self.breakout_len + 1)
        lows = ctx.lows(self.breakout_len + 1)
        if len(highs) < self.breakout_len + 1 or len(lows) < self.breakout_len + 1:
            return None

        prev_channel_high = max(highs[-self.breakout_len - 1:-1])
        prev_channel_low = min(lows[-self.breakout_len - 1:-1])
        current_close = ctx.bar.close

        # Vol collapse history showed long bias had cleaner drift in ETH
        if current_close > prev_channel_high:
            self.spike_observed = False
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=220.0,
                take_profit_bps=480.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "vol_collapse_coil_breakout_high",
                    "short_vol": round(short_vol * 10000, 2),
                    "med_vol": round(med_vol * 10000, 2),
                    "breakout_level": round(prev_channel_high, 2),
                    "close": round(current_close, 2),
                    "bars_post_spike": self.bars_since_spike
                }
            )
        elif current_close < prev_channel_low:
            self.spike_observed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=200.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "vol_collapse_coil_breakout_low",
                    "short_vol": round(short_vol * 10000, 2),
                    "med_vol": round(med_vol * 10000, 2),
                    "breakdown_level": round(prev_channel_low, 2),
                    "close": round(current_close, 2),
                    "bars_post_spike": self.bars_since_spike
                }
            )

        return None