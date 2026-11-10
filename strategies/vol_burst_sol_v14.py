from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SOL Volume Burst Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.vol_multiplier = 2.2
        self.range_multiplier = 1.6
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_trade_bar = -100
        self.entry_bar = -100

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = max(self.vol_period, self.atr_period + 1) + 2
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)
        volumes = ctx.volumes(total_bars_needed)

        current_idx = ctx.bar_index
        has_pos = ctx.has_position()

        # Handle active position management / momentum stall / time-stop
        if has_pos:
            bars_held = current_idx - self.entry_bar
            direction = ctx.position_direction()

            # Time stop
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "exit_price": ctx.bar.close}
                )

            # Momentum stall: price reversal against dominant burst direction
            if direction == "long" and ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) > 0.6 * (ctx.bar.high - ctx.bar.low):
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "close": ctx.bar.close}
                )
            elif direction == "short" and ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.open) > 0.6 * (ctx.bar.high - ctx.bar.low):
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "close": ctx.bar.close}
                )

            return None

        # Cooldown guard
        if current_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Compute volume baseline (excluding current bar)
        prior_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(prior_vols) / len(prior_vols)
        current_vol = volumes[-1]

        if avg_vol <= 0 or current_vol < self.vol_multiplier * avg_vol:
            return None

        # Compute ATR baseline for range expansion (excluding current bar)
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_range = highs[-1] - lows[-1]
        if current_range < self.range_multiplier * atr:
            return None

        # Event boundary: decisive directional close (strong body relative to total candle range)
        body = abs(ctx.bar.close - ctx.bar.open)
        if body < 0.65 * current_range:
            return None

        vol_ratio = current_vol / avg_vol
        range_ratio = current_range / atr
        confidence = min(1.0, 0.5 + 0.1 * (vol_ratio - 2.0) + 0.1 * (range_ratio - 1.5))

        # Long burst: strong bullish close at upper end of candle
        if ctx.bar.close > ctx.bar.open and (ctx.bar.close - lows[-1]) >= 0.8 * current_range:
            self.entry_bar = current_idx
            self.last_trade_bar = current_idx
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "volume_burst_bullish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        # Short burst: strong bearish close at lower end of candle
        if ctx.bar.close < ctx.bar.open and (highs[-1] - ctx.bar.close) >= 0.8 * current_range:
            self.entry_bar = current_idx
            self.last_trade_bar = current_idx
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "volume_burst_bearish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index