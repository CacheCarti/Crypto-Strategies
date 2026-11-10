from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelFade(Strategy):
    METADATA = {
        "name": "SolNarrowChannelFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 95.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.max_width_pct = 1.3
        self.min_width_pct = 0.4
        self.rejection_wick_ratio = 0.25

    def _channel_metrics(self, highs, lows):
        sub_highs = highs[-self.channel_period:]
        sub_lows = lows[-self.channel_period:]
        h_max = max(sub_highs)
        l_min = min(sub_lows)
        mid = (h_max + l_min) / 2.0
        width_pct = ((h_max - l_min) / mid) * 100.0 if mid > 0 else 0.0
        return h_max, l_min, mid, width_pct

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Regime safety: abort in extreme vol or crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit"})
            return None

        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)
        closes = ctx.closes(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Prior 30-bar channel excluding current bar to avoid lookahead contamination
        h_max, l_min, mid, width_pct = self._channel_metrics(highs[:-1], lows[:-1])
        cur_close = ctx.bar.close
        cur_open = ctx.bar.open
        cur_high = ctx.bar.high
        cur_low = ctx.bar.low
        bar_range = cur_high - cur_low

        # Manage existing position exits at midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and cur_close >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "take_profit_midline_long",
                    "price": cur_close,
                    "midline": mid
                })
            elif pos_dir == "short" and cur_close <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "take_profit_midline_short",
                    "price": cur_close,
                    "midline": mid
                })
            return None

        # Cooldown guard after trade exit to prevent churning
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Strict compression filter: narrow tape only, stand down on expansion
        if not (self.min_width_pct <= width_pct <= self.max_width_pct):
            return None

        if bar_range <= 0:
            return None

        # Rejection pin bar boundary mechanics
        lower_wick = min(cur_open, cur_close) - cur_low
        upper_wick = cur_high - max(cur_open, cur_close)
        lower_wick_ratio = lower_wick / bar_range
        upper_wick_ratio = upper_wick / bar_range

        # Edge false breakout / sweep events:
        # Long event: bar sweeps near or below channel low but closes back above with supportive lower wick
        long_sweep = cur_low <= l_min * 1.0005 and cur_close > l_min
        long_rejection = lower_wick_ratio >= self.rejection_wick_ratio and cur_close > cur_open
        if long_sweep and long_rejection and cur_close < mid:
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "channel_low_sweep_pinbar",
                    "channel_low": l_min,
                    "channel_high": h_max,
                    "width_pct": width_pct,
                    "lower_wick_ratio": lower_wick_ratio,
                    "price": cur_close
                }
            )

        # Short event: bar sweeps near or above channel high but closes back below with upper wick rejection
        short_sweep = cur_high >= h_max * 0.9995 and cur_close < h_max
        short_rejection = upper_wick_ratio >= self.rejection_wick_ratio and cur_close < cur_open
        if short_sweep and short_rejection and cur_close > mid:
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "channel_high_sweep_pinbar",
                    "channel_low": l_min,
                    "channel_high": h_max,
                    "width_pct": width_pct,
                    "upper_wick_ratio": upper_wick_ratio,
                    "price": cur_close
                }
            )

        return None