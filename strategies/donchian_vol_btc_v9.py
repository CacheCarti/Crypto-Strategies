from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class DonchianBreakout(Strategy):
    METADATA = {
        "name": "Donchian Breakout Filtered",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 40
        self.vol_multiplier = 1.35
        self.cooldown_bars = 12
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

        # Lookback window excluding the current forming bar
        prior_highs = highs[-self.period - 1:-1]
        prior_lows = lows[-self.period - 1:-1]
        prior_volumes = volumes[-self.period - 1:-1]

        upper_channel = max(prior_highs)
        lower_channel = min(prior_lows)
        midline = (upper_channel + lower_channel) / 2.0

        avg_vol = sum(prior_volumes) / float(self.period)
        curr_close = ctx.bar.close
        curr_vol = ctx.bar.volume

        # Exit logic for active positions on midline cross
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_cross_exit",
                        "close": curr_close,
                        "midline": midline,
                        "upper": upper_channel,
                        "lower": lower_channel,
                    }
                )
            elif pos_dir == "short" and curr_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_cross_exit",
                        "close": curr_close,
                        "midline": midline,
                        "upper": upper_channel,
                        "lower": lower_channel,
                    }
                )
            return None

        # Enforce strict post-exit cooldown to avoid churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        vol_confirmed = curr_vol >= avg_vol * self.vol_multiplier

        # High-conviction breakout entries
        if curr_close > upper_channel and vol_confirmed:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=550.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_upper_breakout_vol_confirmed",
                    "close": curr_close,
                    "upper": upper_channel,
                    "volume_ratio": round(curr_vol / (avg_vol + 1e-8), 2),
                    "midline": midline,
                }
            )

        if curr_close < lower_channel and vol_confirmed:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=550.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_lower_breakout_vol_confirmed",
                    "close": curr_close,
                    "lower": lower_channel,
                    "volume_ratio": round(curr_vol / (avg_vol + 1e-8), 2),
                    "midline": midline,
                }
            )

        return None