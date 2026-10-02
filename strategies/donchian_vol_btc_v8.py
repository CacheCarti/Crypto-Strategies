from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class DonchianBreakoutVariant(Strategy):
    METADATA = {
        "name": "Donchian Breakout Variant",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 36
        self.vol_multiplier = 1.45
        self.cooldown_bars = 16
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.period + 5:
            return None

        highs = ctx.highs(self.period + 1)
        lows = ctx.lows(self.period + 1)
        volumes = ctx.volumes(self.period + 1)

        if len(highs) < self.period + 1 or len(lows) < self.period + 1 or len(volumes) < self.period + 1:
            return None

        prior_highs = highs[:-1]
        prior_lows = lows[:-1]
        prior_volumes = volumes[:-1]

        upper_channel = max(prior_highs)
        lower_channel = min(prior_lows)
        midline = (upper_channel + lower_channel) / 2.0
        avg_volume = sum(prior_volumes) / len(prior_volumes)

        current_close = ctx.bar.close
        current_volume = ctx.bar.volume

        # Exit logic when in position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "exit_long_cross_under_midline",
                        "close": current_close,
                        "midline": midline,
                    },
                )
            elif direction == "short" and current_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "exit_short_cross_over_midline",
                        "close": current_close,
                        "midline": midline,
                    },
                )
            return None

        # Mandatory multi-bar cooldown after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict volume breakout requirement
        volume_confirmed = avg_volume > 0 and current_volume >= (avg_volume * self.vol_multiplier)

        # Long breakout: close breaks above 36-bar upper channel with heavy volume
        if current_close > upper_channel and volume_confirmed:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_upper_channel_breakout",
                    "close": current_close,
                    "upper_channel": upper_channel,
                    "midline": midline,
                    "volume": current_volume,
                    "avg_volume": avg_volume,
                    "vol_ratio": round(current_volume / avg_volume, 2) if avg_volume > 0 else 1.0,
                },
            )

        # Short breakout: close breaks below 36-bar lower channel with heavy volume
        if current_close < lower_channel and volume_confirmed:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_lower_channel_breakout",
                    "close": current_close,
                    "lower_channel": lower_channel,
                    "midline": midline,
                    "volume": current_volume,
                    "avg_volume": avg_volume,
                    "vol_ratio": round(current_volume / avg_volume, 2) if avg_volume > 0 else 1.0,
                },
            )

        return None