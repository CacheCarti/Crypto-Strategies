from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreakBTC(Strategy):
    METADATA = {
        "name": "VolatilityRangeBreakBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20  # Period to define the range
        self.breakout_threshold_factor = 1.5  # Multiplier for ATR to define breakout threshold
        self.cooldown_bars = 12  # Cooldown period after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period: # Ensure we have enough TRs for the average
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1) # Need one more for ATR calc
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate ATR for volatility
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # Define the range using the last 'range_period' bars
        # We need to look back one more bar to identify a 'failed breakout' from the previous bar's range
        # So we define the range from (range_period+1) bars ago up to 2 bars ago.
        # The breakout attempt happens at the previous bar, and we enter if it fails on the current bar.
        range_high = max(highs[-(self.range_period+1):-1])
        range_low = min(lows[-(self.range_period+1):-1])

        prev_close = closes[-2]
        current_close = ctx.bar.close
        
        # Define breakout threshold dynamically with ATR
        breakout_threshold = atr * self.breakout_threshold_factor

        signal = None
        reason = ""
        metadata = {}

        # Long signal: Previous bar broke below range, but current bar closed back strongly into/above range
        if prev_close < range_low - breakout_threshold: # Previous bar broke down
            if current_close > range_low: # Current bar closed back into/above range
                # Ensure the current close is significantly higher than the previous close after the 'failed' breakdown
                if current_close > prev_close + (atr * 0.5):
                    if not ctx.has_position():
                        signal = "long"
                        reason = "failed_breakdown_reversal"
                        metadata = {
                            "range_high": range_high,
                            "range_low": range_low,
                            "prev_close": prev_close,
                            "current_close": current_close,
                            "atr": atr,
                            "breakout_threshold": breakout_threshold,
                            "regime": ctx.regime,
                        }
        
        # Short signal: Previous bar broke above range, but current bar closed back strongly into/below range
        elif prev_close > range_high + breakout_threshold: # Previous bar broke up
            if current_close < range_high: # Current bar closed back into/below range
                # Ensure the current close is significantly lower than the previous close after the 'failed' breakout
                if current_close < prev_close - (atr * 0.5):
                    if not ctx.has_position():
                        signal = "short"
                        reason = "failed_breakout_reversal"
                        metadata = {
                            "range_high": range_high,
                            "range_low": range_low,
                            "prev_close": prev_close,
                            "current_close": current_close,
                            "atr": atr,
                            "breakout_threshold": breakout_threshold,
                            "regime": ctx.regime,
                        }

        if signal:
            self.last_trade_bar = ctx.bar_index
            # Confidence can be higher in normal/calm regimes, lower in volatile/crisis
            confidence = 0.7
            if ctx.regime == "volatile":
                confidence = 0.5
            elif ctx.regime == "crisis":
                confidence = 0.3
            return ctx.signal(
                signal,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": reason, **metadata}
            )

        # If holding a position, check for exit conditions based on range
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Simple exit if price goes far outside the range again (sign of renewed trend)
            if pos_dir == "long" and current_close > range_high + (atr * 2): # If it breaks out strongly again
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_exit_strong_breakout", "current_close": current_close, "range_high": range_high, "atr": atr})
            elif pos_dir == "short" and current_close < range_low - (atr * 2): # If it breaks down strongly again
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_exit_strong_breakdown", "current_close": current_close, "range_low": range_low, "atr": atr})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown (handled by current bar_index)
        pass