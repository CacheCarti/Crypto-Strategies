from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_ema_period = 30
        self.max_hold_bars = 6
        self.cooldown_bars = 8
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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        # Position management: time-based or momentum exhaustion exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            pos_dir = ctx.position_direction()

            # Time stop
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "price": current_close}
                )

            # Reversal bar against position (stalling)
            if pos_dir == "long" and current_close < current_open and (current_high - current_close) > (current_close - current_low):
                if bars_held >= 2:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "momentum_stall_long", "bars_held": bars_held, "price": current_close}
                    )
            elif pos_dir == "short" and current_close > current_open and (current_close - current_low) > (current_high - current_close):
                if bars_held >= 2:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "momentum_stall_short", "bars_held": bars_held, "price": current_close}
                    )
            return None

        # Mandatory Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis Filter (sol_usdc evidence shows momentum fails in high panic/meltdown)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Baseline indicators
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        if avg_vol <= 0:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        ema_trend = self._ema(closes, self.trend_ema_period)
        if ema_trend is None:
            return None

        bar_range = current_high - current_low
        bar_body = abs(current_close - current_open)
        vol_ratio = current_vol / avg_vol
        range_ratio = bar_range / atr

        # Burst conditions: >2.0x volume, wide range (>1.5x ATR), strong body (>55% of range)
        is_vol_burst = vol_ratio >= 2.0
        is_wide_range = range_ratio >= 1.5
        is_directional_body = (bar_body / bar_range) >= 0.55 if bar_range > 0 else False

        if not (is_vol_burst and is_wide_range and is_directional_body):
            return None

        # Trend-aligned entry to avoid noisy whipsaws
        if current_close > current_open and current_close > ema_trend:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.85, 0.5 + 0.1 * min(vol_ratio / 2.0, 3.0)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "price": current_close,
                    "ema_trend": round(ema_trend, 2),
                }
            )

        elif current_close < current_open and current_close < ema_trend:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.85, 0.5 + 0.1 * min(vol_ratio / 2.0, 3.0)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "price": current_close,
                    "ema_trend": round(ema_trend, 2),
                }
            )

        return None