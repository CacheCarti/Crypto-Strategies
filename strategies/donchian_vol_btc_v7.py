from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class DonchianBreakout(Strategy):
    METADATA = {
        "name": "Donchian Breakout Variant",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.vol_mult = 1.40
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.period + 2:
            return None

        highs = ctx.highs(self.period + 1)
        lows = ctx.lows(self.period + 1)
        volumes = ctx.volumes(self.period + 1)
        curr_close = ctx.bar.close
        curr_vol = ctx.bar.volume

        if len(highs) < self.period + 1 or len(lows) < self.period + 1 or len(volumes) < self.period + 1:
            return None

        prior_highs = highs[:-1]
        prior_lows = lows[:-1]
        prior_vols = volumes[:-1]

        upper_band = max(prior_highs)
        lower_band = min(prior_lows)
        midline = (upper_band + lower_band) / 2.0
        avg_vol = sum(prior_vols) / len(prior_vols)

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "donchian_long_midline_cross",
                        "close": curr_close,
                        "midline": midline,
                        "upper_band": upper_band,
                    },
                )
            elif direction == "short" and curr_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "donchian_short_midline_cross",
                        "close": curr_close,
                        "midline": midline,
                        "lower_band": lower_band,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if avg_vol <= 0:
            return None

        vol_confirmed = curr_vol >= avg_vol * self.vol_mult

        if curr_close > upper_band and vol_confirmed:
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "donchian_channel_high_breakout",
                    "close": curr_close,
                    "upper_band": upper_band,
                    "midline": midline,
                    "volume": curr_vol,
                    "avg_volume": avg_vol,
                    "vol_ratio": curr_vol / avg_vol,
                },
            )

        if curr_close < lower_band and vol_confirmed:
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "donchian_channel_low_breakout",
                    "close": curr_close,
                    "lower_band": lower_band,
                    "midline": midline,
                    "volume": curr_vol,
                    "avg_volume": avg_vol,
                    "vol_ratio": curr_vol / avg_vol,
                },
            )

        return None