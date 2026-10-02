from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 160,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 10
        self.baseline_period = 144
        self.channel_period = 12
        self.spike_threshold_mult = 1.85
        self.cooldown_bars = 10
        self.coil_expiry_bars = 24

        self.spike_detected = False
        self.coil_active = False
        self.coil_start_bar = 0
        self.last_exit_bar = -100

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
            else:
                returns.append(0.0)
        if len(returns) < period:
            return None
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_active = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.baseline_period + self.vol_period + 5
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate rolling realized volatility history
        vol_history = []
        for offset in range(self.baseline_period, -1, -1):
            sub_closes = closes[:len(closes) - offset] if offset > 0 else closes
            vol = self._realized_vol(sub_closes, self.vol_period)
            if vol is not None:
                vol_history.append(vol)

        if len(vol_history) < self.baseline_period:
            return None

        current_vol = vol_history[-1]
        baseline_vols = vol_history[:-1]
        median_vol = statistics.median(baseline_vols)

        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol

        # Volatility Spike & Coil State Machine
        if vol_ratio >= self.spike_threshold_mult:
            self.spike_detected = True
            self.coil_active = False

        elif self.spike_detected and vol_ratio <= 1.05:
            # Transition from spike to compression
            self.spike_detected = False
            self.coil_active = True
            self.coil_start_bar = ctx.bar_index

        # Expire stale coil states
        if self.coil_active and (ctx.bar_index - self.coil_start_bar > self.coil_expiry_bars):
            self.coil_active = False

        # If in position, let engine trailing/SL/TP manage or exit on coil expiry reversal
        if ctx.has_position():
            return None

        # Trigger coil release breakouts
        if self.coil_active:
            recent_highs = highs[-self.channel_period - 1:-1]
            recent_lows = lows[-self.channel_period - 1:-1]

            if not recent_highs or not recent_lows:
                return None

            channel_high = max(recent_highs)
            channel_low = min(recent_lows)
            current_close = ctx.bar.close

            # Long breakout on compression release
            if current_close > channel_high:
                self.coil_active = False
                confidence = min(0.90, max(0.60, 0.65 + (current_close - channel_high) / channel_high * 10.0))
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "channel_high": round(channel_high, 2),
                        "close": round(current_close, 2),
                    }
                )

            # Short breakdown on compression release
            elif current_close < channel_low:
                self.coil_active = False
                confidence = min(0.90, max(0.60, 0.65 + (channel_low - current_close) / channel_low * 10.0))
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "post_spike_coil_short_breakdown",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "channel_low": round(channel_low, 2),
                        "close": round(current_close, 2),
                    }
                )

        return None