from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelFade(Strategy):
    METADATA = {
        "name": "SolNarrowChannelFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.max_width_pct = 1.35  # Narrow consolidation filter (chop gate)
        self.min_width_pct = 0.40  # Avoid dead periods where edge is smaller than friction
        self.cooldown_bars = 6     # At least 30m cooldown between trades to avoid overtrading
        self.last_exit_bar = -100

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.period + 10)
        highs = ctx.highs(self.period)
        lows = ctx.lows(self.period)

        if len(closes) < self.period + 8 or len(highs) < self.period or len(lows) < self.period:
            return None

        highest = max(highs)
        lowest = min(lows)
        current_close = closes[-1]
        midline = (highest + lowest) / 2.0
        channel_width = highest - lowest

        if midline <= 0:
            return None

        channel_width_pct = (channel_width / midline) * 100.0

        # Position exit logic: flat when price reaches or crosses the channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_midline_reached",
                    "close": current_close,
                    "midline": midline,
                    "channel_width_pct": channel_width_pct
                })
            elif direction == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_midline_reached",
                    "close": current_close,
                    "midline": midline,
                    "channel_width_pct": channel_width_pct
                })
            return None

        # Mandatory cooldown check after last trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Stand down if channel is widening (trending/breakout phase) or too compressed
        if channel_width_pct > self.max_width_pct or channel_width_pct < self.min_width_pct:
            return None

        # Secondary oscillator filter to ensure exhaustion at edge
        rsi = self._rsi(closes, period=7)
        if rsi is None:
            return None

        # Fading the boundaries: tight band at lower/upper 15% of the channel
        rel_pos = (current_close - lowest) / channel_width if channel_width > 0 else 0.5

        # Long near channel low with short-term oversold confirmation
        if rel_pos <= 0.15 and rsi <= 34.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "range_fade_lower_edge",
                    "channel_width_pct": round(channel_width_pct, 3),
                    "rel_pos": round(rel_pos, 3),
                    "rsi": round(rsi, 2),
                    "midline": round(midline, 3)
                }
            )

        # Short near channel high with short-term overbought confirmation
        if rel_pos >= 0.85 and rsi >= 66.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "range_fade_upper_edge",
                    "channel_width_pct": round(channel_width_pct, 3),
                    "rel_pos": round(rel_pos, 3),
                    "rsi": round(rsi, 2),
                    "midline": round(midline, 3)
                }
            )

        return None