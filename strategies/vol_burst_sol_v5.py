from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
        "required_features": []
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 24
        self.vol_multiplier = 2.75
        self.range_multiplier = 2.20
        self.min_body_ratio = 0.70
        self.max_hold_bars = 8
        self.cooldown_bars = 12
        self.entry_bar_index = -1
        self.cooldown_until_bar = 0

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
        self.entry_bar_index = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = max(self.vol_period, self.atr_period) + 6
        if ctx.bar_index < total_bars_needed:
            return None

        closes = ctx.closes(total_bars_needed)
        opens = ctx.opens(total_bars_needed)
        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)
        volumes = ctx.volumes(total_bars_needed)

        if len(closes) < total_bars_needed:
            return None

        current_high = highs[-1]
        current_low = lows[-1]
        current_open = opens[-1]
        current_close = closes[-1]
        current_vol = volumes[-1]

        bar_range = current_high - current_low
        bar_body = abs(current_close - current_open)
        body_ratio = bar_body / (bar_range + 1e-9)

        prev_volumes = volumes[:-1]
        vol_sma = self._sma(prev_volumes, self.vol_period)
        atr_val = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)

        if vol_sma is None or atr_val is None or atr_val <= 0 or vol_sma <= 0:
            return None

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_index if self.entry_bar_index >= 0 else 0

            if bars_held >= self.max_hold_bars:
                self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_exhaustion_exit",
                        "bars_held": bars_held,
                        "close": current_close,
                        "atr": round(atr_val, 4)
                    }
                )

            if pos_dir == "long":
                if current_close < opens[-1] and current_close < lows[-2]:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_momentum_stall_breakdown",
                            "bars_held": bars_held,
                            "close": current_close,
                            "prev_low": lows[-2]
                        }
                    )
            elif pos_dir == "short":
                if current_close > opens[-1] and current_close > highs[-2]:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_momentum_stall_breakout",
                            "bars_held": bars_held,
                            "close": current_close,
                            "prev_high": highs[-2]
                        }
                    )
            return None

        # Entry Filter & Cooldown Check
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if ctx.regime == "crisis":
            return None

        is_volume_burst = current_vol >= (vol_sma * self.vol_multiplier)
        is_wide_range = bar_range >= (atr_val * self.range_multiplier)
        is_clean_directional_candle = body_ratio >= self.min_body_ratio

        if is_volume_burst and is_wide_range and is_clean_directional_candle:
            vol_ratio = current_vol / vol_sma
            range_ratio = bar_range / atr_val

            # Require breakout past 5-bar high/low to eliminate chop within range
            recent_5_high = max(highs[-6:-1])
            recent_5_low = min(lows[-6:-1])

            if current_close > current_open and current_close > recent_5_high:
                self.entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.80,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "selective_bullish_volume_burst_breakout",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "recent_5_high": recent_5_high,
                        "atr": round(atr_val, 4),
                        "close": current_close
                    }
                )

            elif current_close < current_open and current_close < recent_5_low:
                self.entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.80,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "selective_bearish_volume_burst_breakdown",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "recent_5_low": recent_5_low,
                        "atr": round(atr_val, 4),
                        "close": current_close
                    }
                )

        return None