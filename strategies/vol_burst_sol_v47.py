from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.max_hold_bars = 6
        self.cooldown_period = 5
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time-based stop (stalling mechanism to avoid dead capital)
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held}
                )

            # Reversal bar stalling exit
            cur_close = ctx.bar.close
            cur_open = ctx.bar.open
            if pos_dir == "long" and cur_close < cur_open and bars_held >= 2:
                # If opposing candle forms and wipes out previous bar's progress
                if cur_close < lows[-2]:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "long_momentum_stall", "bars_held": bars_held}
                    )
            elif pos_dir == "short" and cur_close > cur_open and bars_held >= 2:
                if cur_close > highs[-2]:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "short_momentum_stall", "bars_held": bars_held}
                    )
            return None

        # Cooldown guard after exit to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Filter out extreme crisis regimes
        if ctx.regime == "crisis":
            return None

        # Indicator calculations
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        if avg_vol <= 0:
            return None
        vol_ratio = volumes[-1] / avg_vol

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        cur_bar_range = ctx.bar.high - ctx.bar.low
        range_ratio = cur_bar_range / atr

        # Burst conditions: >2.1x volume and >1.6x ATR range
        is_vol_burst = vol_ratio >= 2.1
        is_wide_range = range_ratio >= 1.6

        if not (is_vol_burst and is_wide_range):
            return None

        bar_body = ctx.bar.close - ctx.bar.open
        close_location = (ctx.bar.close - ctx.bar.low) / (cur_bar_range + 1e-9)

        # Bullish burst: strong green candle closing near the high (>70% of bar range)
        if bar_body > 0 and close_location >= 0.70:
            self.entry_bar = ctx.bar_index
            conf = min(0.5 + (vol_ratio - 2.0) * 0.1 + (range_ratio - 1.5) * 0.1, 0.95)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "vol_burst_bullish_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close_loc": round(close_location, 2),
                }
            )

        # Bearish burst: strong red candle closing near the low (<30% of bar range)
        elif bar_body < 0 and close_location <= 0.30:
            self.entry_bar = ctx.bar_index
            conf = min(0.5 + (vol_ratio - 2.0) * 0.1 + (range_ratio - 1.5) * 0.1, 0.95)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "vol_burst_bearish_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close_loc": round(close_location, 2),
                }
            )

        return None