from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_period = 30
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_exit_bar = -999
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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time stop / hold limit
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_hold_exceeded", "bars": bars_in_trade})

            # Momentum stall / reversal guard
            if pos_dir == "long":
                if ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) > 0.6 * (ctx.bar.high - ctx.bar.low):
                    return ctx.signal("flat", confidence=0.65, metadata={"reason": "momentum_stalled_long", "bars": bars_in_trade})
            elif pos_dir == "short":
                if ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.open) > 0.6 * (ctx.bar.high - ctx.bar.low):
                    return ctx.signal("flat", confidence=0.65, metadata={"reason": "momentum_stalled_short", "bars": bars_in_trade})

            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes entirely
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Compute volume and expansion metrics
        recent_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(recent_vols) / len(recent_vols) if len(recent_vols) > 0 else 0.0
        cur_vol = volumes[-1]

        if avg_vol <= 0:
            return None
        vol_ratio = cur_vol / avg_vol

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        body_size = abs(ctx.bar.close - ctx.bar.open)
        range_ratio = bar_range / atr

        # High-conviction volume & range explosion filter
        # Volume > 2.2x and bar range > 1.8x ATR, body represents bulk of range (>= 65%)
        if vol_ratio < 2.2 or range_ratio < 1.8:
            return None

        if bar_range == 0 or (body_size / bar_range) < 0.65:
            return None

        # Trend context alignment
        ema_trend = self._ema(closes, self.trend_period)
        if ema_trend is None:
            return None

        is_bullish_bar = ctx.bar.close > ctx.bar.open and ctx.bar.close > closes[-2]
        is_bearish_bar = ctx.bar.close < ctx.bar.open and ctx.bar.close < closes[-2]

        conf = min(0.90, 0.60 + min(0.30, (vol_ratio - 2.0) * 0.08))

        # Long entry setup: decisive expansion up aligned with macro trend or reclaim
        if is_bullish_bar and ctx.bar.close > ema_trend:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bullish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "ema": round(ema_trend, 2),
                }
            )

        # Short entry setup: decisive expansion down aligned below macro trend
        if is_bearish_bar and ctx.bar.close < ema_trend:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bearish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "ema": round(ema_trend, 2),
                }
            )

        return None