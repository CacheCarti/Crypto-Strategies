from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutReversion(Strategy):
    METADATA = {
        "name": "Failed Breakout Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough bars for 20-period BB and 50-period SMA
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_num_std = 2.0
        self.sma_period = 50
        self.cooldown_bars = 10 # Cooldown period after a trade closes
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = self._sma(slice_c, period)
        std = self._stddev(slice_c, period)
        if mean is None or std is None:
            return None, None, None
        return mean, mean + num_std * std, mean - num_std * std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.sma_period)
        highs = ctx.highs(self.bb_period)
        lows = ctx.lows(self.bb_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Calculate Bollinger Bands
        bb_mid, bb_upper, bb_lower = self._bollinger(closes, self.bb_period, self.bb_num_std)
        if bb_mid is None:
            return None

        # Calculate SMA
        sma_val = self._sma(closes, self.sma_period)
        if sma_val is None:
            return None

        # Failed Breakout Logic
        # Look for a recent candle that broke out of BB, but the current candle is reverting
        
        # Long Entry: Failed Breakout Below (price dropped below lower BB, then quickly recovered)
        # We need at least 2 bars to detect a failed breakout pattern
        if len(closes) >= 2:
            prev_close = closes[-2]
            prev_low = lows[-2]
            
            # Condition 1: Previous bar closed below lower Bollinger Band
            # Condition 2: Current bar has recovered and closed above the lower Bollinger Band
            # Condition 3: Price is generally above the slow SMA (bullish bias for long)
            if (prev_low < bb_lower and current_close > bb_lower and current_close > sma_val and ctx.bar_index > self.last_trade_bar + self.cooldown_bars):
                
                # Further refine by ensuring the previous bar was a significant dip
                if (bb_lower - prev_low) / bb_lower > 0.002: # Ensure a decent dip
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_breakdown_reversion",
                            "current_close": current_close,
                            "bb_lower": bb_lower,
                            "sma50": sma_val,
                            "prev_low": prev_low,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )

            # Short Entry: Failed Breakout Above (price rose above upper BB, then quickly reverted)
            # Conditions are mirrored for short
            prev_high = highs[-2]
            
            # Condition 1: Previous bar closed above upper Bollinger Band
            # Condition 2: Current bar has reverted and closed below the upper Bollinger Band
            # Condition 3: Price is generally below the slow SMA (bearish bias for short)
            if (prev_high > bb_upper and current_close < bb_upper and current_close < sma_val and ctx.bar_index > self.last_trade_bar + self.cooldown_bars):
                
                # Further refine by ensuring the previous bar was a significant spike
                if (prev_high - bb_upper) / bb_upper > 0.002: # Ensure a decent spike
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "failed_breakup_reversion",
                            "current_close": current_close,
                            "bb_upper": bb_upper,
                            "sma50": sma_val,
                            "prev_high": prev_high,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )

        # If holding a position, check for exit conditions (optional, engine handles SL/TP)
        # For simplicity, we let the engine handle SL/TP. The strategy focuses on entry.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on position close to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if a trade was very profitable,
        # increase confidence for the next entry, or if it was a loss, reduce it.