from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 bars on 1h
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 5
        self.cooldown_period = 4
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
        closes = ctx.closes(self.vol_period + 5)
        highs = ctx.highs(self.vol_period + 5)
        lows = ctx.lows(self.vol_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.vol_period + 1:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_hold_bars_reached", "bars_held": bars_held}
                )
            
            # Momentum stall / immediate reversal exit
            c = ctx.bar.close
            o = ctx.bar.open
            if pos_dir == "long" and c < o and bars_held >= 2:
                # Bearish bar stalling the upward thrust
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "bullish_momentum_stalled", "bars_held": bars_held}
                )
            elif pos_dir == "short" and c > o and bars_held >= 2:
                # Bullish bar stalling the downward thrust
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "bearish_momentum_stalled", "bars_held": bars_held}
                )

            return None

        # Cooldown guard: strictly enforce spacing between trades to eliminate overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Calculate volume metrics
        past_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(past_vols) / len(past_vols)
        if avg_vol <= 0:
            return None
        vol_ratio = ctx.bar.volume / avg_vol

        # Calculate range metrics
        atr = self._atr(highs, lows, closes, period=self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        range_ratio = bar_range / atr

        # Event boundary: true volume explosion (>2.2x avg) with outsized range (>1.6x ATR)
        if vol_ratio >= 2.2 and range_ratio >= 1.6:
            # Body must constitute dominant portion of the candle (conviction move, not just a wick)
            body = abs(ctx.bar.close - ctx.bar.open)
            body_ratio = body / bar_range if bar_range > 0 else 0

            if body_ratio >= 0.60:
                is_bullish = ctx.bar.close > ctx.bar.open
                confidence = min(0.9, 0.6 + (vol_ratio - 2.2) * 0.1)

                if is_bullish:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bullish_volume_burst_expansion",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "body_ratio": round(body_ratio, 2),
                            "close": ctx.bar.close
                        }
                    )
                else:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bearish_volume_burst_expansion",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "body_ratio": round(body_ratio, 2),
                            "close": ctx.bar.close
                        }
                    )

        return None