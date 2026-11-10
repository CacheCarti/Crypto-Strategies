from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilitySqueezeBreakout(Strategy):
    METADATA = {
        "name": "Volatility Squeeze Breakout BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour average hold time
        "warmup_bars": 60, # ATR 20 + SMA 20 + stddev 20
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.stddev_period = 20
        self.vol_squeeze_threshold = 0.5 # Ratio of ATR to StdDev for squeeze detection
        self.breakout_multiplier = 1.0 # Multiplier for breakout threshold based on ATR
        self.cooldown_bars = 10 # Cooldown period after a trade (bars)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Use simple moving average for ATR
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate ATR and Standard Deviation
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        stddev_val = self._stddev(closes, self.stddev_period)
        
        if atr_val is None or stddev_val is None or stddev_val == 0:
            return None

        # Squeeze condition: ATR is significantly lower than price standard deviation
        # This indicates a compression of volatility relative to overall price movement
        vol_squeeze_ratio = atr_val / stddev_val
        is_squeezed = vol_squeeze_ratio < self.vol_squeeze_threshold

        # Check for range breakout after a squeeze
        # We need to look for a recent squeeze and then a breakout from that
        # For simplicity, we'll check if the current bar breaks a recent range after a squeeze detection
        
        # Determine breakout thresholds based on recent ATR
        breakout_up_threshold = current_price + atr_val * self.breakout_multiplier
        breakout_down_threshold = current_price - atr_val * self.breakout_multiplier

        # Check for previous squeeze state
        # A simple way to track this is to maintain a `squeezed_state` flag
        # For a more robust solution, one might track the last time `is_squeezed` was true
        # For this example, let's look at the immediate past bar's state
        
        # We need to detect a transition from non-squeezed to squeezed, and then a breakout
        # This requires tracking previous bar's state, which we can do with a class variable.
        # Let's simplify and assume the squeeze is persistent enough that we only need to check current state.
        # A more robust approach would track the duration of the squeeze.

        # To avoid over-trading, we need a cooldown period
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        # Entry logic:
        # 1. Volatility squeeze detected (ATR / StdDev is low)
        # 2. Price breaks out of a recent range (e.g., above/below previous bar's high/low by ATR multiple)
        #    For simplicity, let's use the current bar's close relative to previous bar's close
        
        # To detect a *breakout* more clearly after a squeeze, we need to compare current price
        # to a recent range *after* the squeeze.
        # Let's consider a simple breakout: current close is significantly higher/lower than
        # the close of the bar when the squeeze was first detected (or when it was most pronounced)

        # Let's keep it simple: if squeezed, and then a strong move outside a recent range.
        # This is a bit tricky without storing previous squeeze states.
        # A simple approximation: if currently squeezed, and current bar shows a strong move.
        
        # Let's define the "range" as the last few bars' high/low
        recent_high = max(highs[-5:])
        recent_low = min(lows[-5:])

        long_condition = False
        short_condition = False

        if is_squeezed:
            # Check for breakout from the recent range
            # A strong close above the recent high could be a long breakout
            if current_price > recent_high + atr_val * 0.5: # Breakout above recent high + some buffer
                long_condition = True
            # A strong close below the recent low could be a short breakout
            elif current_price < recent_low - atr_val * 0.5: # Breakout below recent low + some buffer
                short_condition = True

        # Signal generation
        if not ctx.has_position():
            if long_condition:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "volatility_squeeze_long_breakout",
                        "atr": atr_val,
                        "stddev": stddev_val,
                        "vol_squeeze_ratio": vol_squeeze_ratio,
                        "current_price": current_price,
                        "recent_high": recent_high,
                    }
                )
            elif short_condition:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "volatility_squeeze_short_breakout",
                        "atr": atr_val,
                        "stddev": stddev_val,
                        "vol_squeeze_ratio": vol_squeeze_ratio,
                        "current_price": current_price,
                        "recent_low": recent_low,
                    }
                )
        elif ctx.has_position() and ctx.position_direction() == "long" and short_condition:
            # Flip from long to short if a strong short signal appears
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                metadata={
                    "reason": "volatility_squeeze_long_to_short_flip",
                    "atr": atr_val,
                    "stddev": stddev_val,
                    "vol_squeeze_ratio": vol_squeeze_ratio,
                    "current_price": current_price,
                    "recent_low": recent_low,
                }
            )
        elif ctx.has_position() and ctx.position_direction() == "short" and long_condition:
            # Flip from short to long if a strong long signal appears
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                metadata={
                    "reason": "volatility_squeeze_short_to_long_flip",
                    "atr": atr_val,
                    "stddev": stddev_val,
                    "vol_squeeze_ratio": vol_squeeze_ratio,
                    "current_price": current_price,
                    "recent_high": recent_high,
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # You could use this to adapt parameters, but for now, we'll just log
        pass