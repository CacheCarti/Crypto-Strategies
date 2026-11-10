from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelFade(Strategy):
    METADATA = {
        "name": "SolNarrowChannelFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 95.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 40
        self.last_exit_bar = -999
        self.min_channel_bps = 50.0
        self.max_channel_bps = 140.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Disallow new trades during persistent crisis/volatile shocks
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Respect cooldown to limit total trade frequency across 17k bars to 40-90 trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)
        closes = ctx.closes(self.channel_period + 1)

        if len(closes) < self.channel_period + 1:
            return None

        # Lookback over the preceding N bars (excluding the current forming bar)
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        channel_high = max(hist_highs)
        channel_low = min(hist_lows)
        midline = (channel_high + channel_low) / 2.0

        if midline <= 0:
            return None

        channel_width_bps = ((channel_high - channel_low) / midline) * 10000.0

        # Channel must be strictly range-bound: narrow chop, not a breakout
        if not (self.min_channel_bps <= channel_width_bps <= self.max_channel_bps):
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Manage active position: exit cleanly around the channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_reversion",
                        "midline": round(midline, 4),
                        "close": round(curr_close, 4),
                        "channel_width_bps": round(channel_width_bps, 2),
                    }
                )
            elif direction == "short" and curr_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_reversion",
                        "midline": round(midline, 4),
                        "close": round(curr_close, 4),
                        "channel_width_bps": round(channel_width_bps, 2),
                    }
                )
            return None

        # Discrete boundary rejection event triggers:
        # Long sweep: bar dipped at or below channel low, but closed firmly back inside the channel
        bullish_sweep = (curr_low <= channel_low) and (curr_close > channel_low) and (curr_close > prev_close)

        # Short sweep: bar spiked at or above channel high, but closed firmly back inside the channel
        bearish_sweep = (curr_high >= channel_high) and (curr_close < channel_high) and (curr_close < prev_close)

        if bullish_sweep:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "narrow_channel_low_sweep_fade",
                    "channel_low": round(channel_low, 4),
                    "channel_high": round(channel_high, 4),
                    "midline": round(midline, 4),
                    "channel_width_bps": round(channel_width_bps, 2),
                    "close": round(curr_close, 4),
                }
            )

        if bearish_sweep:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "narrow_channel_high_sweep_fade",
                    "channel_low": round(channel_low, 4),
                    "channel_high": round(channel_high, 4),
                    "midline": round(midline, 4),
                    "channel_width_bps": round(channel_width_bps, 2),
                    "close": round(curr_close, 4),
                }
            )

        return None