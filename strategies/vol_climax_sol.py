from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20 # For average volume
        self.range_period = 20 # For average true range
        self.vol_climax_multiplier = 2.5 # Volume must be X times average
        self.range_climax_multiplier = 2.0 # Range must be Y times average
        self.cooldown_bars = 10 # Bars to wait after a position closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate average volume and range
        avg_volume = sum(volumes[-self.vol_period-1:-1]) / self.vol_period if len(volumes) >= self.vol_period + 1 else 0
        avg_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.range_period)

        if avg_volume == 0 or avg_atr is None:
            return None

        current_volume = ctx.bar.volume
        current_range = ctx.bar.high - ctx.bar.low

        # Check for volatility climax on the *previous* bar
        prev_bar_volume = volumes[-2]
        prev_bar_range = highs[-2] - lows[-2]

        is_volume_climax = prev_bar_volume > avg_volume * self.vol_climax_multiplier
        is_range_climax = prev_bar_range > avg_atr * self.range_climax_multiplier

        if not (is_volume_climax or is_range_climax):
            return None # No climax event on the previous bar

        # Now, check the current bar for failure to continue (fade condition)
        # We need to fade the previous bar's direction.
        # If prev_bar was bullish (close > open), we look for current bar to fail upwards (e.g., current close < prev close)
        # If prev_bar was bearish (close < open), we look for current bar to fail downwards (e.g., current close > prev close)

        prev_bar_open = ctx.opens(2)[0]
        prev_bar_close = closes[-2]
        current_close = ctx.bar.close

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Previous bar was bullish climax
        if prev_bar_close > prev_bar_open and current_close < prev_bar_close:
            # Fade long (go short) if the current bar fails to continue the upward momentum
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = 0.65 + (0.15 if is_volume_climax and is_range_climax else 0) # Higher confidence if both climaxes
                reason = "bullish_climax_fade"

        # Previous bar was bearish climax
        elif prev_bar_close < prev_bar_open and current_close > prev_bar_close:
            # Fade short (go long) if the current bar fails to continue the downward momentum
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = 0.65 + (0.15 if is_volume_climax and is_range_climax else 0)
                reason = "bearish_climax_fade"

        if signal_direction:
            # If we have an existing position and want to flip, the engine handles it
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "prev_bar_volume": prev_bar_volume,
                    "avg_volume": avg_volume,
                    "prev_bar_range": prev_bar_range,
                    "avg_atr": avg_atr,
                    "is_volume_climax": is_volume_climax,
                    "is_range_climax": is_range_climax,
                    "prev_bar_close": prev_bar_close,
                    "current_close": current_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last_trade_bar to enforce cooldown
        self.last_trade_bar = ctx.bar_index