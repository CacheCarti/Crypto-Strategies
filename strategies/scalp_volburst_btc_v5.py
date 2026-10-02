from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCVolumeBurstScalper(Strategy):
    METADATA = {
        "name": "BTC Volume Burst Scalper",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.vol_multiplier = 3.6
        self.min_range_bps = 25.0
        self.min_body_ratio = 0.65
        self.max_hold_bars = 4
        self.cooldown_bars = 36
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        vols = ctx.volumes(self.vol_period + 1)
        if len(vols) < self.vol_period + 1:
            return None

        # Manage open position duration exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if self.entry_bar > 0 and bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "burst_time_decay_exit",
                        "bars_held": bars_held,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Hard post-trade cooldown (36 bars = 3 hours on 5m) to avoid trade clusters
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown market regimes
        if ctx.regime == "crisis":
            return None
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        # Baseline volume average (excluding current bar)
        baseline_vols = vols[:-1]
        avg_vol = self._sma(baseline_vols, self.vol_period)
        if avg_vol is None or avg_vol <= 0:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol
        if vol_ratio < self.vol_multiplier:
            return None

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_open = ctx.bar.open
        bar_close = ctx.bar.close
        bar_range = bar_high - bar_low

        if bar_range <= 0 or bar_close <= 0:
            return None

        # Range expansion filter: bar range must be meaningful
        range_bps = (bar_range / bar_close) * 10000.0
        if range_bps < self.min_range_bps:
            return None

        # Displacement body ratio: body must dominate the candle wicks
        body_size = abs(bar_close - bar_open)
        body_ratio = body_size / bar_range
        if body_ratio < self.min_body_ratio:
            return None

        close_loc = (bar_close - bar_low) / bar_range
        confidence = min(0.95, 0.60 + (vol_ratio - self.vol_multiplier) * 0.08)

        # Bullish volume burst: top 15% close, strong bullish body
        if close_loc >= 0.85 and bar_close > bar_open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_loc": round(close_loc, 3),
                    "range_bps": round(range_bps, 1),
                    "body_ratio": round(body_ratio, 2),
                    "price": bar_close
                }
            )

        # Bearish volume burst: bottom 15% close, strong bearish body
        if close_loc <= 0.15 and bar_close < bar_open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_loc": round(close_loc, 3),
                    "range_bps": round(range_bps, 1),
                    "body_ratio": round(body_ratio, 2),
                    "price": bar_close
                }
            )

        return None