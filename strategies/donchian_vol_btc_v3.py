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
        self.lookback = 40
        self.vol_mult = 1.35
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        volumes = ctx.volumes(self.lookback + 1)

        if len(closes) < self.lookback + 2 or len(volumes) < self.lookback + 1:
            return None

        # Donchian channel over prior lookback bars (excluding current bar)
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        highest = max(prior_highs)
        lowest = min(prior_lows)
        midline = (highest + lowest) / 2.0

        current_close = ctx.bar.close
        current_volume = ctx.bar.volume
        vol_avg = sum(volumes[-self.lookback - 1 : -1]) / self.lookback
        vol_ratio = current_volume / vol_avg if vol_avg > 0 else 1.0

        # Position exit logic: close on midline cross
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close < midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "donchian_midline_cross_below",
                        "close": current_close,
                        "midline": midline,
                        "highest": highest,
                        "lowest": lowest,
                    },
                )
            elif direction == "short" and current_close > midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "donchian_midline_cross_above",
                        "close": current_close,
                        "midline": midline,
                        "highest": highest,
                        "lowest": lowest,
                    },
                )
            return None

        # Multi-bar post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        volume_confirmed = vol_ratio >= self.vol_mult

        if current_close > highest and volume_confirmed:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_breakout_high",
                    "close": current_close,
                    "highest": highest,
                    "vol_ratio": vol_ratio,
                    "midline": midline,
                },
            )

        if current_close < lowest and volume_confirmed:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "donchian_breakout_low",
                    "close": current_close,
                    "lowest": lowest,
                    "vol_ratio": vol_ratio,
                    "midline": midline,
                },
            )

        return None