from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 6
        self.cooldown_bars = 10
        self.entry_bar_idx = -1
        self.last_exit_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        volumes = ctx.volumes(35)

        if len(closes) < 30:
            return None

        # Manage open position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 1
            direction = ctx.position_direction()
            
            # Time-based boundary exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "price": ctx.bar.close}
                )

            # Momentum stall / adverse continuation check
            curr_bar_range = ctx.bar.high - ctx.bar.low
            if curr_bar_range > 0:
                body = ctx.bar.close - ctx.bar.open
                if direction == "long" and body < 0 and abs(body) > 0.6 * curr_bar_range:
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "price": ctx.bar.close}
                    )
                elif direction == "short" and body > 0 and body > 0.6 * curr_bar_range:
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "price": ctx.bar.close}
                    )
            return None

        # Check mandatory cooldown
        if (ctx.bar_index - self.last_exit_idx) < self.cooldown_bars:
            return None

        # Compute volume and range statistics
        recent_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0.0
        if avg_vol <= 0:
            return None

        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol

        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        range_ratio = bar_range / atr_val
        body = ctx.bar.close - ctx.bar.open

        # Event criteria: volume > 2.2x avg, wide range (> 1.6x ATR), decisive directional close
        is_vol_burst = vol_ratio >= 2.2
        is_wide_range = range_ratio >= 1.6
        body_to_range = abs(body) / bar_range if bar_range > 0 else 0.0

        if is_vol_burst and is_wide_range and body_to_range >= 0.55:
            # Macro crisis/meltdown filter
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            if body > 0:
                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=280.0,
                    take_profit_bps=520.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "bullish_volume_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_to_range, 2),
                        "price": ctx.bar.close
                    }
                )
            elif body < 0:
                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.70,
                    stop_loss_bps=280.0,
                    take_profit_bps=520.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "bearish_volume_burst",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_to_range, 2),
                        "price": ctx.bar.close
                    }
                )

        return None