from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class DonchianBreakout(Strategy):
    METADATA = {
        "name": "DonchianBreakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)
        closes = ctx.closes(self.period + 2)
        volumes = ctx.volumes(self.period)

        if len(highs) < self.period + 2 or len(volumes) < self.period:
            return None

        # Exclude the last 2 bars to evaluate a strict breakout from the prior range
        upper_channel = max(highs[-self.period - 1 : -1])
        lower_channel = min(lows[-self.period - 1 : -1])
        midline = (upper_channel + lower_channel) / 2.0

        current_close = closes[-1]
        prev_close = closes[-2]
        current_volume = ctx.bar.volume
        avg_volume = sum(volumes) / float(self.period)

        # 1. Manage Active Position (Exit logic)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_cross_exit",
                        "close": current_close,
                        "midline": midline,
                    },
                )
            elif direction == "short" and current_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_cross_exit",
                        "close": current_close,
                        "midline": midline,
                    },
                )
            return None

        # 2. Hard multi-bar cooldown check after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. Entry Signal Generation: require fresh breakout cross + high volume surge
        vol_ratio = (current_volume / avg_volume) if avg_volume > 0 else 1.0
        volume_confirmed = vol_ratio >= 1.25

        # Fresh bullish breakout: previous bar was inside/below channel, current bar crosses above
        if prev_close <= upper_channel and current_close > upper_channel and volume_confirmed:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_fresh_upper_breakout",
                    "close": current_close,
                    "upper_channel": upper_channel,
                    "midline": midline,
                    "vol_ratio": round(vol_ratio, 2),
                },
            )

        # Fresh bearish breakout: previous bar was inside/above channel, current bar crosses below
        if prev_close >= lower_channel and current_close < lower_channel and volume_confirmed:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_fresh_lower_breakout",
                    "close": current_close,
                    "lower_channel": lower_channel,
                    "midline": midline,
                    "vol_ratio": round(vol_ratio, 2),
                },
            )

        return None