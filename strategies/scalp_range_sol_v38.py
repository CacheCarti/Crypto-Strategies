from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelFade(Strategy):
    METADATA = {
        "name": "SolNarrowChannelFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.max_channel_width_pct = 0.016
        self.min_channel_width_pct = 0.005

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Base range channel evaluated over preceding 30 closed bars (excluding current)
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)
        channel_range = channel_high - channel_low

        midline = (channel_high + channel_low) / 2.0
        cur_close = ctx.bar.close

        # Channel width sanity
        if midline <= 0:
            return None
        width_pct = channel_range / midline

        # Dynamic management if already in position
        if ctx.has_position():
            direction = ctx.position_direction()
            # Fade target: exit at midline
            if direction == "long" and cur_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_reached_midline",
                    "close": cur_close,
                    "midline": midline
                })
            elif direction == "short" and cur_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_reached_midline",
                    "close": cur_close,
                    "midline": midline
                })
            return None

        # Mandatory cooldown after exiting
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Stand down if channel is trending or too wide, or completely dried up
        if width_pct > self.max_channel_width_pct or width_pct < self.min_channel_width_pct:
            return None

        # Market regime filter: avoid fading strong breakout/crisis environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.4:
            return None

        # Stricter Event Boundary: False Breakout / Rejection Wick
        # Long Trigger: Prior bar probed below channel_low or tested edge, current bar forms bullish rejection and closes back inside
        prev_low = lows[-2]
        prev_close = closes[-2]
        cur_open = ctx.bar.open
        cur_low = ctx.bar.low

        band_tolerance = channel_range * 0.08
        sweep_depth_max = channel_range * 0.25

        # LONG REVERSAL EVENT:
        # Pierced or tapped near the bottom boundary, but buyer absorption pulled it back into the channel
        long_sweep = (prev_low <= channel_low + band_tolerance or cur_low <= channel_low + band_tolerance)
        long_recovery = cur_close > (channel_low + band_tolerance * 0.5) and cur_close > cur_open
        long_valid_sweep = cur_low >= (channel_low - sweep_depth_max)

        if long_sweep and long_recovery and long_valid_sweep and cur_close < midline:
            return ctx.signal("long", confidence=0.75,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata={
                                  "reason": "rejection_long_channel_sweep",
                                  "channel_low": channel_low,
                                  "channel_high": channel_high,
                                  "width_pct": round(width_pct, 4),
                                  "midline": midline,
                                  "close": cur_close
                              })

        # SHORT REVERSAL EVENT:
        # Pierced or tapped near the top boundary, but seller absorption pulled it back into the channel
        short_sweep = (highs[-2] >= channel_high - band_tolerance or ctx.bar.high >= channel_high - band_tolerance)
        short_recovery = cur_close < (channel_high - band_tolerance * 0.5) and cur_close < cur_open
        short_valid_sweep = ctx.bar.high <= (channel_high + sweep_depth_max)

        if short_sweep and short_recovery and short_valid_sweep and cur_close > midline:
            return ctx.signal("short", confidence=0.75,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata={
                                  "reason": "rejection_short_channel_sweep",
                                  "channel_low": channel_low,
                                  "channel_high": channel_high,
                                  "width_pct": round(width_pct, 4),
                                  "midline": midline,
                                  "close": cur_close
                              })

        return None