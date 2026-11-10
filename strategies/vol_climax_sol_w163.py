from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade_SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended for multi-hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.range_period = 20
        self.cooldown_bars = 10 # Cooldown after a trade exits
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        current_volume = volumes[-1]

        # Calculate average true range (ATR) and average volume
        atr_val = self._atr(highs, lows, closes, self.range_period)
        avg_volume = self._sma(volumes, self.vol_period)

        if atr_val is None or avg_volume is None:
            return None

        # --- Volatility Climax Detection ---
        # Condition 1: Current bar's range is significantly higher than average
        current_range = current_high - current_low
        range_ratio = current_range / atr_val if atr_val > 0 else 0

        # Condition 2: Current bar's volume is significantly higher than average
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        # Look for a "climax" bar followed by a "failure to continue" bar
        if len(closes) >= 2:
            prev_close = closes[-2]
            prev_high = highs[-2]
            prev_low = lows[-2]
            prev_volume = volumes[-2]
            prev_range = prev_high - prev_low

            prev_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.range_period)
            prev_avg_volume = self._sma(volumes[:-1], self.vol_period)

            if prev_atr is None or prev_avg_volume is None:
                return None

            prev_range_ratio = prev_range / prev_atr if prev_atr > 0 else 0
            prev_volume_ratio = prev_volume / prev_avg_volume if prev_avg_volume > 0 else 0

            # Climax bar conditions (previous bar)
            is_climax_bar = (
                prev_range_ratio > 2.5 and # Range is 2.5x average
                prev_volume_ratio > 2.0   # Volume is 2.0x average
            )

            # Failure to continue (current bar)
            # This is where we fade the climax: current bar shows reversal or lack of follow-through
            # For short: previous bar was a strong up move, current bar is lower close / weak follow-through
            # For long: previous bar was a strong down move, current bar is higher close / weak follow-through

            if is_climax_bar:
                # Potential short: previous bar was a strong bullish climax
                if prev_close > closes[-3] and current_close < prev_close: # Previous bar closed up, current bar closed down
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75,
                                          metadata={"reason": "bullish_climax_fade_reversal",
                                                    "prev_range_ratio": prev_range_ratio,
                                                    "prev_volume_ratio": prev_volume_ratio,
                                                    "current_close": current_close,
                                                    "prev_close": prev_close})

                # Potential long: previous bar was a strong bearish climax
                elif prev_close < closes[-3] and current_close > prev_close: # Previous bar closed down, current bar closed up
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75,
                                          metadata={"reason": "bearish_climax_fade_reversal",
                                                    "prev_range_ratio": prev_range_ratio,
                                                    "prev_volume_ratio": prev_volume_ratio,
                                                    "current_close": current_close,
                                                    "prev_close": prev_close})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown
        self.last_trade_bar = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if many losses, increase cooldown
        pass