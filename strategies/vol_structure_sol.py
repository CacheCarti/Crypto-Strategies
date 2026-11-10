from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructureSOL(Strategy):
    METADATA = {
        "name": "VolatilityStructureSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10 # Number of bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars
        self.hh_period = 5 # Period for Higher Highs detection
        self.ll_period = 5 # Period for Lower Lows detection
        self.vol_period = 20 # Period for volatility (ATR)
        self.min_atr_multiplier = 1.5 # Min ATR to consider a significant move
        self.pullback_threshold = 0.618 # Max % pullback from the high/low of the structure
        self.min_structure_bars = 3 # Minimum bars for a valid HH/LL structure

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate ATR for volatility context
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Detect Higher Highs / Higher Lows (Bullish Structure)
        # We need at least self.min_structure_bars for a valid structure
        if len(highs) >= self.hh_period and len(lows) >= self.hh_period:
            recent_highs = highs[-self.hh_period:]
            recent_lows = lows[-self.hh_period:]

            is_hh_hl_structure = True
            for i in range(1, self.hh_period):
                if recent_highs[i] <= recent_highs[i-1] or recent_lows[i] <= recent_lows[i-1]:
                    is_hh_hl_structure = False
                    break

            if is_hh_hl_structure and self.hh_period >= self.min_structure_bars:
                # Check for pullback
                peak_high = recent_highs[-1]
                pullback_low = min(lows[-self.min_structure_bars:]) # Low since the last high
                pullback_percentage = (peak_high - current_price) / (peak_high - pullback_low) if (peak_high - pullback_low) > 0 else 0

                # Ensure current price is a pullback within range and above previous low
                if (0.1 < pullback_percentage < self.pullback_threshold) and current_price > recent_lows[-2]:
                    # Ensure volatility confirms the move
                    if (peak_high - recent_lows[0]) > (self.min_atr_multiplier * current_atr):
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                metadata={
                                    "reason": "bullish_vol_structure_pullback_entry",
                                    "current_price": current_price,
                                    "peak_high": peak_high,
                                    "pullback_percentage": round(pullback_percentage, 3),
                                    "atr": round(current_atr, 2),
                                    "regime": ctx.regime,
                                }
                            )

        # Detect Lower Highs / Lower Lows (Bearish Structure)
        if len(highs) >= self.ll_period and len(lows) >= self.ll_period:
            recent_highs = highs[-self.ll_period:]
            recent_lows = lows[-self.ll_period:]

            is_lh_ll_structure = True
            for i in range(1, self.ll_period):
                if recent_highs[i] >= recent_highs[i-1] or recent_lows[i] >= recent_lows[i-1]:
                    is_lh_ll_structure = False
                    break

            if is_lh_ll_structure and self.ll_period >= self.min_structure_bars:
                # Check for pullback
                trough_low = recent_lows[-1]
                pullback_high = max(highs[-self.min_structure_bars:]) # High since the last low
                pullback_percentage = (current_price - trough_low) / (pullback_high - trough_low) if (pullback_high - trough_low) > 0 else 0

                # Ensure current price is a pullback within range and below previous high
                if (0.1 < pullback_percentage < self.pullback_threshold) and current_price < recent_highs[-2]:
                     # Ensure volatility confirms the move
                    if (recent_highs[0] - trough_low) > (self.min_atr_multiplier * current_atr):
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                metadata={
                                    "reason": "bearish_vol_structure_pullback_entry",
                                    "current_price": current_price,
                                    "trough_low": trough_low,
                                    "pullback_percentage": round(pullback_percentage, 3),
                                    "atr": round(current_atr, 2),
                                    "regime": ctx.regime,
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this exploration strategy yet, just log for now
        pass