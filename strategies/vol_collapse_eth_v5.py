from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PostSpikeCoilStrategy(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 160,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.short_vol_period = 10
        self.long_vol_period = 144
        self.breakout_period = 12
        self.spike_threshold = 1.85
        self.compression_threshold = 1.05
        self.spike_window = 24
        self.cooldown_period = 6

        self.last_spike_bar = -999
        self.last_exit_bar = -999

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            ret = (closes[i] - prev) / prev
            returns.append(ret)
        if len(returns) < period:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_vol_period + 2)
        if len(closes) < self.long_vol_period + 1:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        vol_short = self._realized_vol(closes, self.short_vol_period)
        vol_long = self._realized_vol(closes, self.long_vol_period)

        if vol_short is None or vol_long is None or vol_long == 0:
            return None

        vol_ratio = vol_short / vol_long

        # Detect chaotic expansion spike
        if vol_ratio >= self.spike_threshold:
            self.last_spike_bar = ctx.bar_index

        # Evaluate compression after spike
        bars_since_spike = ctx.bar_index - self.last_spike_bar
        is_post_spike_coil = (0 < bars_since_spike <= self.spike_window) and (
            vol_ratio <= self.compression_threshold
        )

        if ctx.has_position():
            # If in position and coil decays or invalidates, let TP/SL handle exits
            return None

        if not is_post_spike_coil:
            return None

        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)
        if len(highs) < self.breakout_period + 2 or len(lows) < self.breakout_period + 2:
            return None

        # Prior breakout boundaries excluding current candle
        prior_high = max(highs[-self.breakout_period - 1 : -1])
        prior_low = min(lows[-self.breakout_period - 1 : -1])
        current_close = ctx.bar.close

        # Coil Release: Bullish Breakout
        if current_close > prior_high:
            self.last_spike_bar = -999  # Disarm trigger
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakout_long",
                    "vol_ratio": round(vol_ratio, 3),
                    "bars_since_spike": bars_since_spike,
                    "prior_high": round(prior_high, 2),
                    "close": round(current_close, 2),
                },
            )

        # Coil Release: Bearish Breakdown
        elif current_close < prior_low:
            self.last_spike_bar = -999  # Disarm trigger
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_breakdown_short",
                    "vol_ratio": round(vol_ratio, 3),
                    "bars_since_spike": bars_since_spike,
                    "prior_low": round(prior_low, 2),
                    "close": round(current_close, 2),
                },
            )

        return None