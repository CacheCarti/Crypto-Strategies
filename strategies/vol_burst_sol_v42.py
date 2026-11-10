from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentumEvent(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentumEvent",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_period = 8
        self.max_hold_bars = 4
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        volumes = ctx.volumes(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)
        opens = ctx.opens(25)

        current_idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar if self.entry_bar > 0 else 1
            # Exit on max hold duration (event window completed)
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "event_window_expired", "bars_held": bars_held})
            
            # Momentum stalling: exit if counter bar with decent volume
            pos_dir = ctx.position_direction()
            bar_change = ctx.bar.close - ctx.bar.open
            if pos_dir == "long" and bar_change < 0 and ctx.bar.volume > sum(volumes[-6:-1]) / 5.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_stall", "bars_held": bars_held})
            elif pos_dir == "short" and bar_change > 0 and ctx.bar.volume > sum(volumes[-6:-1]) / 5.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_stall", "bars_held": bars_held})
            return None

        # Hard cooldown enforcement
        if (current_idx - self.last_exit_bar) < self.cooldown_period:
            return None

        # Baseline volume and ATR
        vol_hist = volumes[-21:-1]
        if len(vol_hist) < 20:
            return None
        avg_vol = sum(vol_hist) / 20.0
        if avg_vol <= 0:
            return None

        cur_vol = ctx.bar.volume
        vol_ratio = cur_vol / avg_vol

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        cur_range = ctx.bar.high - ctx.bar.low
        body_size = abs(ctx.bar.close - ctx.bar.open)
        range_ratio = cur_range / atr

        # Event criteria: volume burst > 2.2x and range > 1.8x ATR, with solid candle body (not a doji)
        is_vol_burst = vol_ratio >= 2.2
        is_wide_range = range_ratio >= 1.8
        is_directional_body = (body_size / cur_range) >= 0.55 if cur_range > 0 else False

        if not (is_vol_burst and is_wide_range and is_directional_body):
            return None

        # Directional entry
        if ctx.bar.close > ctx.bar.open:
            self.entry_bar = current_idx
            return ctx.signal(
                "long",
                confidence=min(0.5 + (vol_ratio - 2.0) * 0.1, 0.85),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4),
                }
            )
        elif ctx.bar.close < ctx.bar.open:
            self.entry_bar = current_idx
            return ctx.signal(
                "short",
                confidence=min(0.5 + (vol_ratio - 2.0) * 0.1, 0.85),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "atr": round(atr, 4),
                }
            )

        return None