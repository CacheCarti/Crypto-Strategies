from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        self.entry_bar_idx = -100
        self.last_exit_bar_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar_idx = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        volumes = ctx.volumes(30)

        if len(closes) < self.METADATA["warmup_bars"] or len(volumes) < 25:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            pos_dir = ctx.position_direction()

            # Time-based exit to prevent stagnant holds and ensure independent trades
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_reached",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })

            # Momentum stall exit: close reversing past prior bar's midpoint
            prev_mid = (highs[-2] + lows[-2]) / 2.0
            if pos_dir == "long" and ctx.bar.close < prev_mid and ctx.bar.close < ctx.bar.open:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_momentum_stall",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            elif pos_dir == "short" and ctx.bar.close > prev_mid and ctx.bar.close > ctx.bar.open:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_momentum_stall",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar_idx < self.cooldown_bars:
            return None

        # Avoid trading in extreme crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume burst calculation: current volume > 2.2x 20-bar average (excluding current bar)
        prior_vols = volumes[-21:-1]
        avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if avg_vol <= 0:
            return None
        vol_ratio = ctx.bar.volume / avg_vol

        # Range expansion calculation: current bar range > 1.6x ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None
        bar_range = ctx.bar.high - ctx.bar.low
        range_ratio = bar_range / atr

        # Require significant body relative to range (conviction candle)
        body = abs(ctx.bar.close - ctx.bar.open)
        body_to_range = body / bar_range if bar_range > 0 else 0

        # Burst conditions: strong volume, wide expansion, solid directional body
        if vol_ratio >= 2.1 and range_ratio >= 1.5 and body_to_range >= 0.55:
            # Scaled confidence
            confidence = min(0.9, 0.6 + (vol_ratio - 2.0) * 0.05 + (range_ratio - 1.5) * 0.05)

            if ctx.bar.close > ctx.bar.open:
                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_vol_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_to_range": round(body_to_range, 2),
                        "close": ctx.bar.close,
                        "atr": round(atr, 4)
                    }
                )
            elif ctx.bar.close < ctx.bar.open:
                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_vol_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_to_range": round(body_to_range, 2),
                        "close": ctx.bar.close,
                        "atr": round(atr, 4)
                    }
                )

        return None