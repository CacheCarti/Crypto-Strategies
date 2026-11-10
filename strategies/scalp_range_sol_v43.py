from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 45
        self.last_exit_bar = -100
        self.min_channel_width_pct = 0.007
        self.max_channel_width_pct = 0.022

    def _rsi(self, closes, period=9):
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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 10 or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        current_price = ctx.bar.close
        highest = max(highs)
        lowest = min(lows)
        midline = (highest + lowest) / 2.0
        channel_span = highest - lowest

        if channel_span <= 0.0 or lowest <= 0.0:
            return None

        width_ratio = channel_span / midline

        # Exit at midline if holding a position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_price >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_reached_midline",
                    "price": current_price,
                    "midline": midline,
                    "channel_width_pct": width_ratio * 100.0
                })
            elif direction == "short" and current_price <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_reached_midline",
                    "price": current_price,
                    "midline": midline,
                    "channel_width_pct": width_ratio * 100.0
                })
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Stand down if channel is too wide (breakout/trend) or too flat (friction trap)
        if width_ratio > self.max_channel_width_pct or width_ratio < self.min_channel_width_pct:
            return None

        # Avoid crisis volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, period=9)
        if rsi is None:
            return None

        pos_in_channel = (current_price - lowest) / channel_span

        # Fade range low: price deep in the lower 12% of channel + oversold bounce check
        if pos_in_channel <= 0.12 and rsi < 28.0:
            return ctx.signal("long", confidence=0.75, stop_loss_bps=90.0, take_profit_bps=140.0, metadata={
                "reason": "fade_channel_low_oversold",
                "price": current_price,
                "channel_pos": round(pos_in_channel, 3),
                "rsi": round(rsi, 2),
                "channel_width_pct": round(width_ratio * 100.0, 3)
            })

        # Fade range high: price deep in the upper 12% of channel + overbought rejection check
        if pos_in_channel >= 0.88 and rsi > 72.0:
            return ctx.signal("short", confidence=0.75, stop_loss_bps=90.0, take_profit_bps=140.0, metadata={
                "reason": "fade_channel_high_overbought",
                "price": current_price,
                "channel_pos": round(pos_in_channel, 3),
                "rsi": round(rsi, 2),
                "channel_width_pct": round(width_ratio * 100.0, 3)
            })

        return None