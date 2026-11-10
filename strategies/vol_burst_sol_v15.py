from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.volume_mult = 2.2
        self.range_mult = 1.6
        self.cooldown_bars = 6
        self.max_hold_bars = 5
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)
        volumes = ctx.volumes(self.lookback + 5)

        if len(closes) < self.lookback + 1:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based expiration for independent trade structure
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_bars_reached", "bars_held": bars_held})

            # Momentum stall: opposing directional close
            curr_bar = ctx.bar
            if pos_dir == "long" and curr_bar.close < curr_bar.open:
                if bars_held >= 2:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "momentum_stall_bear_close", "bars_held": bars_held})
            elif pos_dir == "short" and curr_bar.close > curr_bar.open:
                if bars_held >= 2:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "momentum_stall_bull_close", "bars_held": bars_held})

            return None

        # Cooldown guard after exit to keep trades independent and prevent churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis regime avoidance
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Volume baseline calculation
        prior_vols = volumes[-self.lookback - 1:-1]
        avg_vol = sum(prior_vols) / len(prior_vols)
        current_vol = volumes[-1]

        if avg_vol <= 0 or (current_vol / avg_vol) < self.volume_mult:
            return None

        # Range expansion check
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        bar_range = highs[-1] - lows[-1]
        if bar_range < self.range_mult * atr:
            return None

        # Directional conviction: close must reflect displacement
        curr_open = ctx.bar.open
        curr_close = ctx.bar.close
        body = abs(curr_close - curr_open)

        # Body must constitute at least 50% of the entire bar range
        if bar_range == 0 or (body / bar_range) < 0.50:
            return None

        vol_ratio = current_vol / avg_vol
        range_ratio = bar_range / atr
        confidence = min(0.85, 0.50 + 0.05 * (vol_ratio - 2.0) + 0.05 * (range_ratio - 1.5))

        if curr_close > curr_open:
            self.entry_bar = ctx.bar_index
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
                    "atr": round(atr, 4),
                    "price": curr_close,
                }
            )
        elif curr_close < curr_open:
            self.entry_bar = ctx.bar_index
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
                    "atr": round(atr, 4),
                    "price": curr_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index