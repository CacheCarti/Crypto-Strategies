from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class DonchianBreakout(Strategy):
    METADATA = {
        "name": "Donchian Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 36
        self.vol_mult = 1.35
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 1)
        highs = ctx.highs(self.period + 1)
        lows = ctx.lows(self.period + 1)
        volumes = ctx.volumes(self.period + 1)

        if len(closes) < self.period + 1:
            return None

        # Historical channel bounds excluding current candle
        past_highs = highs[:-1]
        past_lows = lows[:-1]
        past_vols = volumes[:-1]

        upper_channel = max(past_highs)
        lower_channel = min(past_lows)
        midline = (upper_channel + lower_channel) / 2.0

        avg_vol = sum(past_vols) / len(past_vols) if len(past_vols) > 0 else 1.0
        current_close = closes[-1]
        current_vol = volumes[-1]

        # 1. Position Management & Midline Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
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
                    confidence=0.6,
                    metadata={
                        "reason": "short_midline_cross_exit",
                        "close": current_close,
                        "midline": midline,
                    },
                )
            return None

        # 2. Strict Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. High-Conviction Breakout Entries with Volume Surge Filter
        vol_confirmed = current_vol >= (avg_vol * self.vol_mult)

        if current_close > upper_channel and vol_confirmed:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_upper_breakout_vol_confirmed",
                    "close": current_close,
                    "upper_channel": upper_channel,
                    "midline": midline,
                    "volume_ratio": round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0,
                },
            )

        if current_close < lower_channel and vol_confirmed:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_lower_breakout_vol_confirmed",
                    "close": current_close,
                    "lower_channel": lower_channel,
                    "midline": midline,
                    "volume_ratio": round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0,
                },
            )

        return None