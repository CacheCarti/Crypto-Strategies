from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 125.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.max_width_pct = 1.35
        self.min_width_pct = 0.40
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

    def _rsi(self, closes, period=8):
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
        closes = ctx.closes(self.period + 10)
        highs = ctx.highs(self.period)
        lows = ctx.lows(self.period)

        if len(closes) < self.period + 10 or len(highs) < self.period or len(lows) < self.period:
            return None

        highest = max(highs)
        lowest = min(lows)
        midline = (highest + lowest) / 2.0
        range_span = highest - lowest

        if midline <= 0:
            return None

        current_close = ctx.bar.close
        width_pct = (range_span / midline) * 100.0

        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long" and (current_close >= midline or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midline_reached" if current_close >= midline else "long_time_stop",
                    "close": current_close,
                    "midline": midline,
                    "bars_held": bars_held
                })

            if direction == "short" and (current_close <= midline or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midline_reached" if current_close <= midline else "short_time_stop",
                    "close": current_close,
                    "midline": midline,
                    "bars_held": bars_held
                })

            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if width_pct > self.max_width_pct or width_pct < self.min_width_pct:
            return None

        rsi = self._rsi(closes, period=8)
        if rsi is None:
            return None

        lower_zone = lowest + 0.12 * range_span
        upper_zone = highest - 0.12 * range_span

        if current_close <= lower_zone and rsi < 32.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.82,
                stop_loss_bps=95.0,
                take_profit_bps=125.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_range_low_oversold",
                    "close": current_close,
                    "channel_low": lowest,
                    "channel_high": highest,
                    "width_pct": round(width_pct, 2),
                    "rsi": round(rsi, 1)
                }
            )

        if current_close >= upper_zone and rsi > 68.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.82,
                stop_loss_bps=95.0,
                take_profit_bps=125.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_range_high_overbought",
                    "close": current_close,
                    "channel_low": lowest,
                    "channel_high": highest,
                    "width_pct": round(width_pct, 2),
                    "rsi": round(rsi, 1)
                }
            )

        return None