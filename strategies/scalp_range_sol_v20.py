from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 90.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 7
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[-i] - closes[-i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        if losses == 0.0:
            return 100.0
        rs = (gains / period) / (losses / period)
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)
        closes = ctx.closes(self.channel_period + 1)

        if len(closes) <= self.channel_period:
            return None

        # Exclude the current live bar for channel boundaries to avoid lookahead/boundary creep
        channel_high = max(highs[:-1])
        channel_low = min(lows[:-1])
        midline = (channel_high + channel_low) / 2.0
        channel_range = channel_high - channel_low

        if midline <= 0:
            return None

        channel_width_pct = (channel_range / midline) * 100.0
        current_close = ctx.bar.close

        # Position exit logic: mean reversion back to channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_hit_midline",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            elif direction == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_hit_midline",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            return None

        # Mandatory cooldown post-exit to prevent churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime gate: only trade in calm or normal regimes to avoid trending expansions
        regime = ctx.market.get("regime", "NORMAL").upper()
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Narrow range filter: channel must be between 0.6% and 1.8% of midline
        if channel_width_pct < 0.6 or channel_width_pct > 1.8:
            return None

        rsi = self._rsi(closes, self.rsi_period)

        # Long entry: price touches lower 12% of channel and RSI shows oversold bounce
        lower_threshold = channel_low + 0.12 * channel_range
        if current_close <= lower_threshold and rsi <= 35.0:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=120.0,
                take_profit_bps=90.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_channel_low",
                    "close": current_close,
                    "channel_low": channel_low,
                    "channel_high": channel_high,
                    "channel_width_pct": channel_width_pct,
                    "rsi": rsi,
                }
            )

        # Short entry: price touches upper 12% of channel and RSI shows overbought rejection
        upper_threshold = channel_high - 0.12 * channel_range
        if current_close >= upper_threshold and rsi >= 65.0:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=120.0,
                take_profit_bps=90.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_channel_high",
                    "close": current_close,
                    "channel_low": channel_low,
                    "channel_high": channel_high,
                    "channel_width_pct": channel_width_pct,
                    "rsi": rsi,
                }
            )

        return None