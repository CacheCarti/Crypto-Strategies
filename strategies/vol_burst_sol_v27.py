from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.cooldown_bars = 8
        self.max_hold_bars = 6
        self.entry_bar = -1
        self.last_exit_bar = -100

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
        warmup = max(self.vol_period, self.atr_period) + 2
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        volumes = ctx.volumes(warmup)

        if len(closes) < warmup or len(volumes) < warmup:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close
            curr_open = ctx.bar.open
            direction = ctx.position_direction()

            # Exit on momentum stalling or fixed bar hold
            stalled = False
            if direction == "long" and curr_close < curr_open and bars_held >= 2:
                stalled = True
            elif direction == "short" and curr_close > curr_open and bars_held >= 2:
                stalled = True

            if bars_held >= self.max_hold_bars or stalled:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_exceeded" if bars_held >= self.max_hold_bars else "momentum_stall",
                        "bars_held": bars_held,
                        "close": curr_close,
                        "entry_bar": self.entry_bar
                    }
                )
            return None

        # Enforce cooldown period after exiting
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis avoidance
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Volume baseline calculation
        prior_volumes = volumes[-(self.vol_period + 1):-1]
        avg_vol = sum(prior_volumes) / self.vol_period
        if avg_vol <= 0:
            return None

        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol

        # Range vs ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_bar_range = highs[-1] - lows[-1]
        range_ratio = current_bar_range / atr

        # Burst conditions: >2.2x volume AND >1.6x ATR range
        if vol_ratio >= 2.2 and range_ratio >= 1.6:
            bar_open = ctx.bar.open
            bar_close = ctx.bar.close
            body = abs(bar_close - bar_open)

            # Ensure directionality: body must account for at least 60% of total bar range
            if body / current_bar_range >= 0.6:
                confidence = min(0.9, 0.5 + (vol_ratio / 10.0))
                self.entry_bar = ctx.bar_index

                if bar_close > bar_open:
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bullish_volume_burst",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "atr": round(atr, 2),
                            "close": bar_close
                        }
                    )
                elif bar_close < bar_open:
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bearish_volume_burst",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "atr": round(atr, 2),
                            "close": bar_close
                        }
                    )

        return None