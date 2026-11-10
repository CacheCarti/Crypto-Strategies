from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_lookback = 20
        self.atr_lookback = 14
        self.cooldown_period = 6
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = max(self.vol_lookback, self.atr_lookback) + 2
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)
        volumes = ctx.volumes(total_bars_needed)

        cur_bar = ctx.bar
        cur_index = ctx.bar_index

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = cur_index - self.entry_bar if self.entry_bar > 0 else 1
            
            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "close": cur_bar.close}
                )

            # Momentum stall / reversal exit
            if pos_dir == "long" and cur_bar.close < lows[-2]:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_stall", "low_break": lows[-2], "close": cur_bar.close}
                )
            elif pos_dir == "short" and cur_bar.close > highs[-2]:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_stall", "high_break": highs[-2], "close": cur_bar.close}
                )

            return None

        # Check post-exit cooldown
        if cur_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid crisis/meltdown turbulence
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume Burst Detection (> 2.3x relative to 20-bar rolling average volume)
        recent_vols = volumes[-self.vol_lookback - 1 : -1]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0.0
        if avg_vol <= 0:
            return None
        vol_mult = cur_bar.volume / avg_vol
        if vol_mult < 2.3:
            return None

        # Unusually Wide Range Detection (> 1.8x ATR)
        atr = self._atr(highs, lows, closes, period=self.atr_lookback)
        if atr is None or atr <= 0:
            return None
        bar_range = cur_bar.high - cur_bar.low
        range_mult = bar_range / atr
        if range_mult < 1.8:
            return None

        # Strong directional thrust: body must dominate range (at least 60% of candle)
        body = abs(cur_bar.close - cur_bar.open)
        if body < 0.60 * bar_range:
            return None

        # Directional entry
        if cur_bar.close > cur_bar.open:
            # Bullish expansion: close should finish near the high of the bar
            if (cur_bar.high - cur_bar.close) > 0.30 * bar_range:
                return None
            self.entry_bar = cur_index
            self.entry_price = cur_bar.close
            confidence = min(0.9, 0.55 + (vol_mult - 2.3) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_conviction_vol_burst_bull",
                    "vol_mult": round(vol_mult, 2),
                    "range_mult": round(range_mult, 2),
                    "atr": round(atr, 4),
                    "close": cur_bar.close
                }
            )
        elif cur_bar.close < cur_bar.open:
            # Bearish expansion: close should finish near the low of the bar
            if (cur_bar.close - cur_bar.low) > 0.30 * bar_range:
                return None
            self.entry_bar = cur_index
            self.entry_price = cur_bar.close
            confidence = min(0.9, 0.55 + (vol_mult - 2.3) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_conviction_vol_burst_bear",
                    "vol_mult": round(vol_mult, 2),
                    "range_mult": round(range_mult, 2),
                    "atr": round(atr, 4),
                    "close": cur_bar.close
                }
            )

        return None