from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SOL Volume Burst Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 22
        self.atr_period = 16
        self.vol_mult = 2.15
        self.range_mult = 1.70
        self.cooldown_bars = 4
        self.max_hold_bars = 7
        self.entry_bar_idx = -100
        self.last_exit_bar_idx = -100

    def _atr(self, highs, lows, closes, period: int = 16) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar_idx = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume
        bar_range = current_high - current_low

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, 14)
        if atr is None or atr <= 0.0 or rsi is None:
            return None

        # Exclude current bar to compute baseline volume
        past_vols = volumes[-(self.vol_period + 1):-1]
        avg_vol = sum(past_vols) / len(past_vols) if past_vols else 0.0
        vol_ratio = current_vol / avg_vol if avg_vol > 0.0 else 0.0
        range_ratio = bar_range / atr

        # Active position management: time-based exit or momentum stall
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_idx

            # Exit on max hold duration
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar_idx = ctx.bar_index
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "time_horizon_reached",
                    "bars_held": bars_held,
                    "rsi": round(rsi, 2),
                    "close": current_close
                })

            # Long stall / exhaustion exit
            if pos_dir == "long":
                if rsi > 78.0 or (bars_held >= 2 and current_close < lows[-2]):
                    self.last_exit_bar_idx = ctx.bar_index
                    return ctx.signal("flat", confidence=0.70, metadata={
                        "reason": "long_momentum_stalled",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "close": current_close
                    })

            # Short stall / exhaustion exit
            elif pos_dir == "short":
                if rsi < 22.0 or (bars_held >= 2 and current_close > highs[-2]):
                    self.last_exit_bar_idx = ctx.bar_index
                    return ctx.signal("flat", confidence=0.70, metadata={
                        "reason": "short_momentum_stalled",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "close": current_close
                    })

            return None

        # Cooldown guard after position exits
        if (ctx.bar_index - self.last_exit_bar_idx) < self.cooldown_bars:
            return None

        # Crisis filter: skip entries during extreme meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Entry triggers: volume burst + wide range expansion
        is_vol_burst = vol_ratio >= self.vol_mult
        is_wide_range = range_ratio >= self.range_mult

        if not (is_vol_burst and is_wide_range and bar_range > 0.0):
            return None

        # Bullish burst: strong bullish candle closing in the top 30% of its range
        if current_close > current_open and (current_close - current_low) / bar_range >= 0.70:
            if rsi < 74.0:
                self.entry_bar_idx = ctx.bar_index
                confidence = min(0.90, max(0.60, 0.55 + 0.10 * (vol_ratio / 2.0)))
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_volume_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "rsi": round(rsi, 2),
                        "atr": round(atr, 4),
                        "price": current_close
                    }
                )

        # Bearish burst: strong bearish candle closing in the bottom 30% of its range
        elif current_close < current_open and (current_high - current_close) / bar_range >= 0.70:
            if rsi > 26.0:
                self.entry_bar_idx = ctx.bar_index
                confidence = min(0.90, max(0.60, 0.55 + 0.10 * (vol_ratio / 2.0)))
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_volume_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "rsi": round(rsi, 2),
                        "atr": round(atr, 4),
                        "price": current_close
                    }
                )

        return None