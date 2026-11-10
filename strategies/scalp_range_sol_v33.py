from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 40
        self.last_exit_bar = -999
        self.min_range_pct = 0.006
        self.max_range_pct = 0.016

    def _rsi(self, closes, period=10):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 12)
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)

        if len(closes) < self.channel_period + 12:
            return None

        # Exclude current bar to evaluate true 30-bar pre-existing channel
        chan_high = max(highs[-self.channel_period - 1:-1])
        chan_low = min(lows[-self.channel_period - 1:-1])
        midline = (chan_high + chan_low) / 2.0
        range_span = chan_high - chan_low

        if chan_low <= 0:
            return None

        range_pct = range_span / chan_low
        current_close = ctx.bar.close

        # Position exit logic: flat when price reaches or crosses channel midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "reached_midline_target", "close": current_close, "midline": midline}
                )
            if pos_dir == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "reached_midline_target", "close": current_close, "midline": midline}
                )
            return None

        # Mandatory cooldown post-exit to ensure rarity & prevent friction death
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out trending/expanding regimes and dead tapes
        if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
            return None

        # Only operate in calm or normal volatility regimes
        if ctx.regime in ["volatile", "crisis"]:
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        # Fade conditions: price touching extreme edge (within 8% band) + momentum confirmation
        upper_threshold = chan_high - 0.08 * range_span
        lower_threshold = chan_low + 0.08 * range_span

        # Fade the bottom: oversold reversal inside tight sideways band
        if current_close <= lower_threshold and rsi <= 34.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_channel_low_fade",
                    "close": current_close,
                    "chan_low": chan_low,
                    "midline": midline,
                    "range_pct": round(range_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        # Fade the top: overbought reversal inside tight sideways band
        if current_close >= upper_threshold and rsi >= 66.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_channel_high_fade",
                    "close": current_close,
                    "chan_high": chan_high,
                    "midline": midline,
                    "range_pct": round(range_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        return None