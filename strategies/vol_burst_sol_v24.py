from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        volumes = ctx.volumes(35)

        if len(closes) < 30 or len(volumes) < 30:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_bar = ctx.bar
            direction = ctx.position_direction()

            # Time-based stop / momentum stall exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "close": curr_bar.close}
                )

            # Reversal bar (stall) exit
            if direction == "long" and curr_bar.close < curr_bar.open and (curr_bar.high - curr_bar.close) > (curr_bar.close - curr_bar.low):
                if bars_held >= 2:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "close": curr_bar.close}
                    )
            elif direction == "short" and curr_bar.close > curr_bar.open and (curr_bar.close - curr_bar.low) > (curr_bar.high - curr_bar.close):
                if bars_held >= 2:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "close": curr_bar.close}
                    )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volume baseline calculation (past 20 bars excluding current)
        vol_baseline = sum(volumes[-21:-1]) / 20.0
        if vol_baseline <= 0:
            return None

        current_vol = volumes[-1]
        vol_ratio = current_vol / vol_baseline

        # Event boundary: strictly require volume burst >= 2.2x
        if vol_ratio < 2.2:
            return None

        # Volatility & range filter
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        curr_range = highs[-1] - lows[-1]
        range_to_atr = curr_range / atr

        # Require an unusually wide bar (>= 1.8x ATR)
        if range_to_atr < 1.8:
            return None

        body = abs(closes[-1] - ctx.opens(1)[-1])
        # Body dominance: ensure directional commitment, not a long-wick indecision bar
        if curr_range <= 0 or (body / curr_range) < 0.60:
            return None

        # Check market crisis regime to avoid toxic spread events
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry signal based on impulse direction
        confidence = min(0.50 + (vol_ratio - 2.0) * 0.10 + (range_to_atr - 1.5) * 0.10, 0.90)

        if closes[-1] > ctx.opens(1)[-1]:
            # Clean breakout impulse long
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_volume_burst_impulse",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(range_to_atr, 2),
                    "close": closes[-1],
                }
            )
        elif closes[-1] < ctx.opens(1)[-1]:
            # Clean breakout impulse short
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_volume_burst_impulse",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_to_atr": round(range_to_atr, 2),
                    "close": closes[-1],
                }
            )

        return None