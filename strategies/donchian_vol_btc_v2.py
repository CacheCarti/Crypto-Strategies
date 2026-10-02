from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class DonchianBreakoutVariant(Strategy):
    METADATA = {
        "name": "Donchian Breakout Variant",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 36
        self.vol_mult = 1.40
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.period + 1)
        lows = ctx.lows(self.period + 1)
        volumes = ctx.volumes(self.period + 1)

        if len(highs) < self.period + 1:
            return None

        # Previous bars defining the channel
        prev_highs = highs[:-1]
        prev_lows = lows[:-1]
        prev_vols = volumes[:-1]

        upper_band = max(prev_highs)
        lower_band = min(prev_lows)
        midline = (upper_band + lower_band) / 2.0
        avg_volume = sum(prev_vols) / len(prev_vols)

        close = ctx.bar.close
        volume = ctx.bar.volume

        # Exit management: close position when crossing the channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_midline_exit",
                        "close": close,
                        "midline": midline,
                    },
                )
            elif direction == "short" and close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_midline_exit",
                        "close": close,
                        "midline": midline,
                    },
                )
            return None

        # Hard cooldown check after previous exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Strict volume confirmation on new breakout
        vol_confirmed = avg_volume > 0 and volume >= (avg_volume * self.vol_mult)

        if close > upper_band and vol_confirmed:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_breakout_long",
                    "close": close,
                    "upper_band": upper_band,
                    "volume": volume,
                    "avg_volume": avg_volume,
                    "vol_ratio": volume / avg_volume if avg_volume > 0 else 1.0,
                },
            )

        if close < lower_band and vol_confirmed:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_breakout_short",
                    "close": close,
                    "lower_band": lower_band,
                    "volume": volume,
                    "avg_volume": avg_volume,
                    "vol_ratio": volume / avg_volume if avg_volume > 0 else 1.0,
                },
            )

        return None