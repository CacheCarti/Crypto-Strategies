from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 190,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_window = 14
        self.baseline_window = 168
        self.breakout_window = 14
        self.spike_mult = 1.85
        self.compression_mult = 0.95
        self.max_coil_age = 20
        self.cooldown_period = 8

        self.rv_history = []
        self.spike_seen = False
        self.bars_since_spike = 999
        self.coil_armed = False
        self.bars_since_coil = 999
        self.last_exit_bar = -999

    def _calc_rv(self, closes: list, window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - window, len(closes))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.baseline_window + self.rv_window + 5
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        current_rv = self._calc_rv(closes, self.rv_window)
        if current_rv is None:
            return None

        self.rv_history.append(current_rv)
        if len(self.rv_history) > self.baseline_window:
            self.rv_history.pop(0)

        if len(self.rv_history) < self.baseline_window:
            return None

        sorted_rv = sorted(self.rv_history)
        median_rv = sorted_rv[len(sorted_rv) // 2]
        if median_rv <= 1e-7:
            return None

        # Detect volatility transitions: Chaos -> Compression
        if current_rv > self.spike_mult * median_rv:
            self.spike_seen = True
            self.bars_since_spike = 0
            self.coil_armed = False
        else:
            self.bars_since_spike += 1

        if self.spike_seen and self.bars_since_spike <= 30:
            if current_rv < self.compression_mult * median_rv:
                self.coil_armed = True
                self.bars_since_coil = 0
                self.spike_seen = False
        
        if self.coil_armed:
            self.bars_since_coil += 1
            if self.bars_since_coil > self.max_coil_age:
                self.coil_armed = False

        if ctx.has_position():
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        if not self.coil_armed:
            return None

        # Coil breakout release trigger
        lookback_highs = highs[-(self.breakout_window + 1):-1]
        lookback_lows = lows[-(self.breakout_window + 1):-1]
        if len(lookback_highs) < self.breakout_window or len(lookback_lows) < self.breakout_window:
            return None

        breakout_high = max(lookback_highs)
        breakout_low = min(lookback_lows)
        current_close = ctx.bar.close

        compression_ratio = current_rv / median_rv
        confidence = max(0.60, min(0.85, 1.0 - (compression_ratio * 0.3)))

        if current_close > breakout_high:
            self.coil_armed = False
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakout_high",
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "breakout_high": breakout_high,
                    "close": current_close,
                    "bars_in_coil": self.bars_since_coil,
                }
            )

        if current_close < breakout_low:
            self.coil_armed = False
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakout_low",
                    "current_rv": current_rv,
                    "median_rv": median_rv,
                    "breakout_low": breakout_low,
                    "close": current_close,
                    "bars_in_coil": self.bars_since_coil,
                }
            )

        return None