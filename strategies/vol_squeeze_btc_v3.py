from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilitySqueezeBreakout(Strategy):
    METADATA = {
        "name": "Volatility Squeeze Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Aim for a few hours hold
        "warmup_bars": 60, # Need enough bars for ATR and Bollinger
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.bollinger_period = 20
        self.bollinger_num_std = 2.0
        self.squeeze_lookback = 10 # Bars to check for squeeze
        self.breakout_confirmation_bars = 2 # How many bars to confirm breakout
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # State variables for detecting squeeze and breakout
        self.in_squeeze = False
        self.squeeze_start_bar = -1
        self.breakout_direction = None # "long" or "short"

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Calculate indicators
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        bb_mean, bb_upper, bb_lower = self._bollinger(closes, self.bollinger_period, self.bollinger_num_std)

        if current_atr is None or bb_mean is None:
            return None

        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Squeeze Detection: Bollinger Bands inside Keltner Channels (simplified to just tight BB for now)
        # We look for periods where the Bollinger Bands are unusually narrow.
        # A simple proxy for this is when the current price range (high-low) is small relative to ATR,
        # or when the distance between BB_upper and BB_lower is contracting.
        # For this strategy, we'll focus on the contraction of Bollinger Bands.

        # Calculate recent Bollinger Band widths
        bb_widths = []
        for i in range(self.bollinger_period, len(closes) + 1):
            _, upper, lower = self._bollinger(closes[:i], self.bollinger_period, self.bollinger_num_std)
            if upper is not None and lower is not None:
                bb_widths.append(upper - lower)
        
        # Ensure we have enough data for squeeze lookback
        if len(bb_widths) < self.squeeze_lookback + 1:
            return None

        # Check for squeeze: current BB width is significantly smaller than recent average
        # Or, more simply, current BB width is at a recent minimum
        current_bb_width = bb_upper - bb_lower
        recent_bb_widths = bb_widths[-self.squeeze_lookback-1:-1] # Exclude current bar for comparison
        
        # A squeeze is detected if the current width is near the minimum of the recent widths
        is_squeezing = current_bb_width < min(recent_bb_widths) * 1.05 # Allow some wiggle room

        if is_squeezing and not self.in_squeeze:
            self.in_squeeze = True
            self.squeeze_start_bar = ctx.bar_index
            self.breakout_direction = None # Reset breakout direction
            # ctx.log(f"Squeeze detected at bar {ctx.bar_index} with BB width: {current_bb_width:.2f}")
            return None # Wait for breakout

        if self.in_squeeze:
            # Squeeze is ongoing, check for breakout
            
            # Condition 1: Price closes above upper band or below lower band
            # Condition 2: Confirmation from subsequent bars
            
            if ctx.bar.close > bb_upper:
                # Potential long breakout
                self.breakout_direction = "long"
            elif ctx.bar.close < bb_lower:
                # Potential short breakout
                self.breakout_direction = "short"
            else:
                # Price is back within bands, squeeze might be continuing or failed breakout
                self.breakout_direction = None

            # If a breakout direction is established, check for confirmation
            if self.breakout_direction:
                # Check for confirmation over the next few bars
                if ctx.bar_index >= self.squeeze_start_bar + self.breakout_confirmation_bars:
                    # Check if price stays outside the band for confirmation_bars
                    confirmed = True
                    for i in range(self.breakout_confirmation_bars):
                        if self.breakout_direction == "long" and closes[-(i+1)] <= bb_upper:
                            confirmed = False
                            break
                        if self.breakout_direction == "short" and closes[-(i+1)] >= bb_lower:
                            confirmed = False
                            break
                    
                    if confirmed:
                        # Breakout confirmed, issue signal
                        confidence = min(0.8, (current_bb_width / (bb_upper - bb_lower)) * 0.5 + 0.3) # Confidence based on how far it broke
                        
                        signal_reason = f"vol_squeeze_breakout_{self.breakout_direction}"
                        
                        self.in_squeeze = False # Reset squeeze state
                        self.last_trade_bar = ctx.bar_index # Set cooldown
                        
                        return ctx.signal(
                            self.breakout_direction,
                            confidence=confidence,
                            metadata={
                                "reason": signal_reason,
                                "current_price": current_price,
                                "bb_mean": bb_mean,
                                "bb_upper": bb_upper,
                                "bb_lower": bb_lower,
                                "current_bb_width": current_bb_width,
                                "atr": current_atr,
                            }
                        )
            
            # If the squeeze has been ongoing for too long without a clear breakout, reset
            if ctx.bar_index - self.squeeze_start_bar > self.bollinger_period * 2: # Max squeeze duration
                self.in_squeeze = False
                self.breakout_direction = None

        # If we have a position, don't issue new entry signals
        if ctx.has_position():
            return None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state if necessary, or log for adaptive sizing
        # self.last_trade_bar = ctx.bar_index # Cooldown already handled in on_bar for entries
        pass