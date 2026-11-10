from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 10
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.max_channel_width_pct = 1.35
        self.min_channel_width_pct = 0.40

    def _rsi(self, closes, period=10):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)

        if len(closes) < self.channel_period + 1:
            return None

        # Exclude current forming bar from channel extremes to prevent lookahead bias
        hist_highs = highs[-self.channel_period - 1:-1]
        hist_lows = lows[-self.channel_period - 1:-1]
        hh = max(hist_highs)
        ll = min(hist_lows)
        midline = (hh + ll) / 2.0
        curr_price = ctx.bar.close

        # Manage active position: exit when target midline reached
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_price >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_midline_target_hit", "midline": round(midline, 3), "price": curr_price})
            elif direction == "short" and curr_price <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_midline_target_hit", "midline": round(midline, 3), "price": curr_price})
            return None

        # Strict cooldown enforcement to limit churn and friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Stand down in crisis or extreme volatility regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None
        if ctx.regime in ("volatile", "crisis"):
            return None

        # Avoid extreme fear or extreme greed cascades
        fg = ctx.features.get("fear_greed_index", 50)
        if fg < 25 or fg > 75:
            return None

        # Channel width check: must be a compact sideways tape
        if midline <= 0:
            return None
        channel_width_pct = ((hh - ll) / midline) * 100.0
        if channel_width_pct > self.max_channel_width_pct or channel_width_pct < self.min_channel_width_pct:
            return None

        # Momentum oscillator confirmation
        rsi_val = self._rsi(closes, self.rsi_period)

        # Thresholds: within the outer 12% boundary of the channel
        channel_span = hh - ll
        long_threshold = ll + (channel_span * 0.12)
        short_threshold = hh - (channel_span * 0.12)

        # Fading the lower boundary
        if curr_price <= long_threshold and rsi_val <= 38.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low_reversion",
                    "price": curr_price,
                    "channel_low": round(ll, 3),
                    "channel_high": round(hh, 3),
                    "width_pct": round(channel_width_pct, 2),
                    "rsi": round(rsi_val, 1)
                }
            )

        # Fading the upper boundary
        if curr_price >= short_threshold and rsi_val >= 62.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high_reversion",
                    "price": curr_price,
                    "channel_low": round(ll, 3),
                    "channel_high": round(hh, 3),
                    "width_pct": round(channel_width_pct, 2),
                    "rsi": round(rsi_val, 1)
                }
            )

        return None