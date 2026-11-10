from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 168
        self.channel_period = 12
        self.cooldown_bars = 6
        self.max_hold_bars = 8

        self.last_exit_bar = -999
        self.entry_bar = -999
        self.spike_active = False

    def _realized_vol(self, closes: list, window: int = 12) -> float:
        if len(closes) < window + 1:
            return 0.0
        log_returns = []
        for i in range(len(closes) - window, len(closes)):
            prev = closes[i - 1]
            curr = closes[i]
            if prev > 0 and curr > 0:
                log_returns.append(math.log(curr / prev))
            else:
                log_returns.append(0.0)
        mean_ret = sum(log_returns) / len(log_returns)
        var = sum((r - mean_ret) ** 2 for r in log_returns) / len(log_returns)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.vol_period + 20, self.METADATA["warmup_bars"])
        closes = ctx.closes(warmup)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < warmup or len(highs) < self.channel_period + 2:
            return None

        # Manage existing position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_hold_exceeded", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute current 12-bar realized vol
        current_vol = self._realized_vol(closes, window=12)

        # Compute historical vol series across the 168-bar lookback (stride of 3 to save cpu)
        vol_history = []
        for offset in range(0, self.vol_period, 3):
            sub_closes = closes[-(offset + 13): len(closes) - offset]
            if len(sub_closes) >= 13:
                vol_history.append(self._realized_vol(sub_closes, window=12))

        if not vol_history:
            return None

        vol_history.sort()
        med_vol = vol_history[len(vol_history) // 2]
        if med_vol <= 0:
            return None

        vol_ratio = current_vol / med_vol

        # Track spike and collapse states
        if vol_ratio > 1.8:
            self.spike_active = True

        # Coil triggers only after a confirmed spike followed by collapse below median
        coil_active = self.spike_active and (vol_ratio < 1.0)

        if not coil_active:
            return None

        # Lookback channel excluding the current forming bar
        channel_high = max(highs[-self.channel_period - 1: -1])
        channel_low = min(lows[-self.channel_period - 1: -1])
        current_close = ctx.bar.close

        # Coil release breakout logic
        if current_close > channel_high:
            self.spike_active = False  # Reset state on breakout
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_high_break",
                    "vol_ratio": round(vol_ratio, 3),
                    "channel_high": round(channel_high, 2),
                    "close": round(current_close, 2),
                }
            )

        if current_close < channel_low:
            self.spike_active = False  # Reset state on breakout
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_low_break",
                    "vol_ratio": round(vol_ratio, 3),
                    "channel_low": round(channel_low, 2),
                    "close": round(current_close, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.spike_active = False