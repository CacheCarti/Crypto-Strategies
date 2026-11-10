from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowChannelRangeFade(Strategy):
    METADATA = {
        "name": "SolNarrowChannelRangeFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.max_channel_width_pct = 1.3
        self.min_channel_width_pct = 0.4
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.rsi_period = 7

    def _rsi(self, closes, period):
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
        closes = ctx.closes(self.period + self.rsi_period + 2)
        if len(closes) < self.period + self.rsi_period:
            return None

        highs = ctx.highs(self.period)
        lows = ctx.lows(self.period)
        if len(highs) < self.period or len(lows) < self.period:
            return None

        # Exclude current bar to avoid self-referencing boundary
        hist_highs = highs[:-1]
        hist_lows = lows[:-1]
        channel_high = max(hist_highs)
        channel_low = min(hist_lows)
        midline = (channel_high + channel_low) / 2.0

        if midline <= 0:
            return None

        channel_width_pct = ((channel_high - channel_low) / midline) * 100.0
        current_close = ctx.bar.close

        # Manage active position: exit when reaching midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_hit_midline",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_hit_midline",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            return None

        # Stand down if in cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Stand down in crisis/high volatility or trending channels
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        if channel_width_pct > self.max_channel_width_pct or channel_width_pct < self.min_channel_width_pct:
            return None

        # Ensure no explosive multi-bar trend underway (ADX proxy via consecutive directional moves)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Edge distance relative to channel range
        channel_range = channel_high - channel_low
        if channel_range <= 0:
            return None

        pos_in_range = (current_close - channel_low) / channel_range

        # Edge fade: Long when near bottom + oversold RSI reversal hook
        if pos_in_range <= 0.12 and rsi < 36:
            conf = min(0.85, max(0.5, (0.15 - pos_in_range) * 3.0 + 0.5))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low",
                    "channel_width_pct": channel_width_pct,
                    "pos_in_range": pos_in_range,
                    "rsi": rsi,
                    "channel_low": channel_low,
                    "midline": midline,
                }
            )

        # Edge fade: Short when near top + overbought RSI reversal hook
        if pos_in_range >= 0.88 and rsi > 64:
            conf = min(0.85, max(0.5, (pos_in_range - 0.85) * 3.0 + 0.5))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high",
                    "channel_width_pct": channel_width_pct,
                    "pos_in_range": pos_in_range,
                    "rsi": rsi,
                    "channel_high": channel_high,
                    "midline": midline,
                }
            )

        return None