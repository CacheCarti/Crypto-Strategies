from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 12
        self.history_period = 168
        self.channel_period = 12
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.state = "IDLE"
        self.spike_bar = -999

    def _calc_rv(self, closes: list) -> float:
        if len(closes) < 2:
            return 0.0
        rets = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                rets.append(abs(closes[i] - prev) / prev)
        return sum(rets) / len(rets) if rets else 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.history_period + self.rv_period + 5
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute rolling short-term realized volatility over history window
        rv_series = []
        for offset in range(self.history_period):
            end_idx = len(closes) - offset
            start_idx = end_idx - self.rv_period
            sub = closes[start_idx:end_idx]
            rv_series.append(self._calc_rv(sub))

        current_rv = rv_series[0]
        sorted_rv = sorted(rv_series)
        med_rv = sorted_rv[len(sorted_rv) // 2]
        p85_rv = sorted_rv[int(len(sorted_rv) * 0.85)]

        # State transition: IDLE -> SPIKED -> ARMED
        if self.state == "IDLE":
            if current_rv >= p85_rv and current_rv > 0:
                self.state = "SPIKED"
                self.spike_bar = ctx.bar_index
        elif self.state == "SPIKED":
            if ctx.bar_index - self.spike_bar > 48:
                self.state = "IDLE"
            elif current_rv <= med_rv:
                self.state = "ARMED"
        elif self.state == "ARMED":
            if ctx.bar_index - self.spike_bar > 72:
                self.state = "IDLE"
            elif current_rv > p85_rv:
                self.state = "SPIKED"
                self.spike_bar = ctx.bar_index

        if self.state != "ARMED":
            return None

        # Coil breakout check on 12-bar channel (excluding current bar)
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)
        if len(highs) < self.channel_period + 1 or len(lows) < self.channel_period + 1:
            return None

        prev_channel_high = max(highs[:-1])
        prev_channel_low = min(lows[:-1])
        current_close = ctx.bar.close

        ratio = current_rv / med_rv if med_rv > 0 else 1.0
        confidence = max(0.55, min(0.85, 0.55 + (1.0 - min(ratio, 1.0)) * 0.3))

        if current_close > prev_channel_high:
            self.state = "IDLE"
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakout_high",
                    "current_rv": current_rv,
                    "med_rv": med_rv,
                    "prev_high": prev_channel_high,
                    "close": current_close,
                },
            )

        if current_close < prev_channel_low:
            self.state = "IDLE"
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakout_low",
                    "current_rv": current_rv,
                    "med_rv": med_rv,
                    "prev_low": prev_channel_low,
                    "close": current_close,
                },
            )

        return None