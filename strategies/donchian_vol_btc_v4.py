from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianBreakoutVariant(Strategy):
    METADATA = {
        "name": "Donchian Breakout Variant",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.vol_period = 48
        self.vol_mult = 1.35
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)
        volumes = ctx.volumes(self.vol_period)

        if len(highs) < self.period + 2 or len(lows) < self.period + 2 or len(volumes) < self.vol_period:
            return None

        prior_high = max(highs[-self.period - 1:-1])
        prior_low = min(lows[-self.period - 1:-1])
        midline = (prior_high + prior_low) / 2.0

        vol_sma = sum(volumes) / float(len(volumes))
        current_close = ctx.bar.close
        current_vol = ctx.bar.volume

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_cross_exit",
                        "price": current_close,
                        "midline": midline,
                        "prior_high": prior_high,
                        "prior_low": prior_low,
                    },
                )
            elif direction == "short" and current_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_cross_exit",
                        "price": current_close,
                        "midline": midline,
                        "prior_high": prior_high,
                        "prior_low": prior_low,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        vol_confirmed = current_vol > (vol_sma * self.vol_mult)

        if current_close > prior_high and vol_confirmed:
            vol_ratio = current_vol / max(vol_sma, 1e-9)
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                metadata={
                    "reason": "donchian_upper_breakout_vol_confirmed",
                    "price": current_close,
                    "breakout_level": prior_high,
                    "midline": midline,
                    "vol_ratio": vol_ratio,
                },
            )

        if current_close < prior_low and vol_confirmed:
            vol_ratio = current_vol / max(vol_sma, 1e-9)
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                metadata={
                    "reason": "donchian_lower_breakout_vol_confirmed",
                    "price": current_close,
                    "breakout_level": prior_low,
                    "midline": midline,
                    "vol_ratio": vol_ratio,
                },
            )

        return None