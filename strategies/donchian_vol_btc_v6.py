from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class DonchianBreakoutVolume(Strategy):
    METADATA = {
        "name": "Donchian Breakout with Volume Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.vol_mult = 1.40
        self.cooldown_bars = 16
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)
        volumes = ctx.volumes(self.period + 2)

        if len(closes) < self.period + 1:
            return None

        # Lookback channels excluding current forming bar
        lookback_highs = highs[-(self.period + 1):-1]
        lookback_lows = lows[-(self.period + 1):-1]
        lookback_vols = volumes[-(self.period + 1):-1]

        upper_channel = max(lookback_highs)
        lower_channel = min(lookback_lows)
        midline = (upper_channel + lower_channel) / 2.0

        avg_vol = sum(lookback_vols) / len(lookback_vols)
        current_close = ctx.bar.close
        current_volume = ctx.bar.volume

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "donchian_midline_cross_below",
                        "close": current_close,
                        "midline": midline,
                        "upper_channel": upper_channel,
                        "lower_channel": lower_channel
                    }
                )
            elif direction == "short" and current_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "donchian_midline_cross_above",
                        "close": current_close,
                        "midline": midline,
                        "upper_channel": upper_channel,
                        "lower_channel": lower_channel
                    }
                )
            return None

        # Strict multi-bar cooldown gate to prevent overtrading and chop
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        vol_confirmed = avg_vol > 0 and (current_volume >= avg_vol * self.vol_mult)
        vol_ratio = current_volume / avg_vol if avg_vol > 0 else 1.0

        # Long breakout on strong volume
        if current_close > upper_channel and vol_confirmed:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                metadata={
                    "reason": "donchian_upper_breakout_vol_confirmed",
                    "close": current_close,
                    "upper_channel": upper_channel,
                    "midline": midline,
                    "vol_ratio": round(vol_ratio, 2)
                }
            )

        # Short breakdown on strong volume
        if current_close < lower_channel and vol_confirmed:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                metadata={
                    "reason": "donchian_lower_breakdown_vol_confirmed",
                    "close": current_close,
                    "lower_channel": lower_channel,
                    "midline": midline,
                    "vol_ratio": round(vol_ratio, 2)
                }
            )

        return None