from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolumeBurstContinuation(Strategy):
    METADATA = {
        "name": "BTC Volume Burst Continuation Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 170.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.vol_mult = 4.2
        self.atr_period = 14
        self.atr_min_mult = 1.75
        self.extreme_ratio = 0.15
        self.min_body_ratio = 0.60
        self.trend_ema_period = 30
        self.cooldown_bars = 24
        self.max_hold_bars = 4

        self.last_trade_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_ema_period + 10)
        highs = ctx.highs(self.trend_ema_period + 10)
        lows = ctx.lows(self.trend_ema_period + 10)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.trend_ema_period + 2 or len(volumes) < self.vol_period + 2:
            return None

        # Position management: time-based exit for short burst duration
        if ctx.has_position():
            if self.entry_bar == -999:
                self.entry_bar = ctx.bar_index - 1

            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "burst_time_exhaustion",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                        "direction": direction,
                    }
                )
            return None

        # Hard cooldown check after last trade exit/entry
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume spike condition (compare to prior average, excluding trigger bar)
        past_volumes = volumes[-(self.vol_period + 1):-1]
        avg_vol = sum(past_volumes) / len(past_volumes) if len(past_volumes) > 0 else 0
        if avg_vol <= 0:
            return None

        vol_ratio = ctx.bar.volume / avg_vol
        if vol_ratio < self.vol_mult:
            return None

        # Range expansion check
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        if bar_range < self.atr_min_mult * atr:
            return None

        body_size = abs(ctx.bar.close - ctx.bar.open)
        if bar_range <= 0 or (body_size / bar_range) < self.min_body_ratio:
            return None

        ema_trend = self._ema(closes, self.trend_ema_period)
        if ema_trend is None:
            return None

        # Entry boundaries (top/bottom 15% extreme close)
        upper_threshold = ctx.bar.high - (bar_range * self.extreme_ratio)
        lower_threshold = ctx.bar.low + (bar_range * self.extreme_ratio)

        conf = min(0.90, 0.65 + 0.05 * min(vol_ratio - self.vol_mult, 3.0))

        # Long burst: strong bullish candle closing near high, above EMA trend
        if ctx.bar.close >= upper_threshold and ctx.bar.close > ctx.bar.open and ctx.bar.close > ema_trend:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bullish_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "close": ctx.bar.close,
                    "ema_trend": round(ema_trend, 2),
                }
            )

        # Short burst: strong bearish candle closing near low, below EMA trend
        if ctx.bar.close <= lower_threshold and ctx.bar.close < ctx.bar.open and ctx.bar.close < ema_trend:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bearish_continuation",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(bar_range / atr, 2),
                    "close": ctx.bar.close,
                    "ema_trend": round(ema_trend, 2),
                }
            )

        return None