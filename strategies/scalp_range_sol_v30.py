from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolScalpRangeFade(Strategy):
    METADATA = {
        "name": "SolScalpRangeFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.min_channel_pct = 0.50
        self.max_channel_pct = 1.35

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
        if len(closes) < self.channel_period + 12:
            return None

        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)
        if len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        highest = max(highs)
        lowest = min(lows)
        channel_span = highest - lowest
        current_close = closes[-1]

        if current_close <= 0:
            return None

        channel_pct = (channel_span / current_close) * 100.0
        midline = (highest + lowest) / 2.0

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "reached_midline_long", "close": current_close, "midline": midline}
                )
            elif pos_dir == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "reached_midline_short", "close": current_close, "midline": midline}
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime != "neutral":
            return None

        if not (self.min_channel_pct <= channel_pct <= self.max_channel_pct):
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        buffer = 0.12 * channel_span

        if current_close <= (lowest + buffer) and rsi < 36.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_support_rebound",
                    "channel_pct": channel_pct,
                    "rsi": rsi,
                    "lowest": lowest,
                    "highest": highest,
                    "close": current_close
                }
            )

        if current_close >= (highest - buffer) and rsi > 64.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_resistance_rebound",
                    "channel_pct": channel_pct,
                    "rsi": rsi,
                    "lowest": lowest,
                    "highest": highest,
                    "close": current_close
                }
            )

        return None