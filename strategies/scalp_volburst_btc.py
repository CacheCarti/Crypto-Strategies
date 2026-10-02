from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolumeBurstContinuationScalp(Strategy):
    METADATA = {
        "name": "VolumeBurstContinuationScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.ema_trend_period = 50
        self.vol_mult_threshold = 3.5
        self.min_range_bps = 25.0
        self.min_body_ratio = 0.65
        self.max_hold_bars = 4
        self.cooldown_bars = 24
        self.entry_bar_idx = -999
        self.last_exit_bar = -999

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_trend_period + 5)
        if len(closes) < self.ema_trend_period:
            return None

        # Exit position on time exhaustion
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_scalp_exhaustion",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Hard multi-bar cooldown after exit or prior entry
        if (ctx.bar_index - self.entry_bar_idx) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip meltdown and crisis environments
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Check volume spike relative to prior baseline
        volumes = ctx.volumes(self.vol_period + 1)
        if len(volumes) < self.vol_period + 1:
            return None

        prev_volumes = volumes[:-1]
        avg_vol = self._sma(prev_volumes, self.vol_period)
        if avg_vol is None or avg_vol <= 0:
            return None

        current_vol = ctx.bar.volume
        vol_ratio = current_vol / avg_vol
        if vol_ratio < self.vol_mult_threshold:
            return None

        # Candle structural analysis
        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_close = ctx.bar.close
        bar_open = ctx.bar.open
        bar_range = bar_high - bar_low

        if bar_range <= 0 or bar_close <= 0:
            return None

        range_bps = (bar_range / bar_close) * 10000.0
        if range_bps < self.min_range_bps:
            return None

        body = abs(bar_close - bar_open)
        body_ratio = body / bar_range
        if body_ratio < self.min_body_ratio:
            return None

        close_location = (bar_close - bar_low) / bar_range
        ema_trend = self._ema(closes, self.ema_trend_period)
        if ema_trend is None:
            return None

        confidence = min(0.90, max(0.65, 0.65 + (vol_ratio - self.vol_mult_threshold) * 0.04))

        # Bullish Burst: Volume spike + close at top decile + body confirmation + aligned with trend
        if close_location >= 0.85 and bar_close > bar_open and bar_close > ema_trend:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_location": round(close_location, 3),
                    "body_ratio": round(body_ratio, 2),
                    "range_bps": round(range_bps, 1),
                    "ema50": round(ema_trend, 2),
                    "close": bar_close,
                },
            )

        # Bearish Burst: Volume spike + close at bottom decile + body confirmation + aligned with trend
        if close_location <= 0.15 and bar_close < bar_open and bar_close < ema_trend:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "close_location": round(close_location, 3),
                    "body_ratio": round(body_ratio, 2),
                    "range_bps": round(range_bps, 1),
                    "ema50": round(ema_trend, 2),
                    "close": bar_close,
                },
            )

        return None