from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralPullback(Strategy):
    METADATA = {
        "name": "Structural Pullback ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average
        "warmup_bars": 100, # Need enough for multiple EMAs and structure
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.medium_ema_period = 50
        self.slow_ema_period = 100
        self.pullback_threshold_bps = 50 # How much price needs to pull back in bps from high
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade immediately

        # Variables to track structure
        self.last_high = 0.0
        self.last_low = 0.0
        self.higher_highs = 0
        self.higher_lows = 0
        self.in_pullback = False
        self.pullback_start_price = 0.0
        self.pullback_high_price = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + self.cooldown_bars)
        highs = ctx.highs(self.slow_ema_period + self.cooldown_bars)
        lows = ctx.lows(self.slow_ema_period + self.cooldown_bars)

        if len(closes) < self.slow_ema_period:
            return None

        current_price = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        fast_ema = self._ema(closes, self.fast_ema_period)
        medium_ema = self._ema(closes, self.medium_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or medium_ema is None or slow_ema is None:
            return None

        # Update structure tracking
        if ctx.bar_index > self.warmup_bars: # Only track structure after warmup
            if current_high > self.last_high and current_low > self.last_low:
                self.higher_highs += 1
                self.higher_lows += 1
            elif current_high > self.last_high:
                self.higher_highs += 1
            elif current_low > self.last_low:
                self.higher_lows += 1
            else: # Structure broken or sideways
                self.higher_highs = 0
                self.higher_lows = 0
                self.in_pullback = False # Reset pullback state

            self.last_high = current_high
            self.last_low = current_low

        # Entry logic
        if not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None # Cooldown active

            # Confirm uptrend: price above EMAs and EMAs stacked (fast > medium > slow)
            uptrend_confirmed = (
                current_price > fast_ema and
                fast_ema > medium_ema and
                medium_ema > slow_ema and
                self.higher_highs >= 2 and self.higher_lows >= 2 # Require at least two HH/HL for confirmation
            )

            if uptrend_confirmed:
                # Detect start of a pullback
                if not self.in_pullback and current_price < closes[-2]: # Price starts to drop
                    self.in_pullback = True
                    self.pullback_start_price = closes[-2]
                    self.pullback_high_price = closes[-1] # The high before pullback started

                # If in a pullback, check for reversal
                if self.in_pullback:
                    # Price must have pulled back a bit
                    pullback_depth_bps = (self.pullback_high_price - current_price) / self.pullback_high_price * 10000
                    
                    # Check if price is turning up and holding above medium EMA
                    # and has pulled back sufficiently but not too much (e.g. not below slow ema)
                    if (current_price > closes[-2] and # Price turned up
                        pullback_depth_bps >= self.pullback_threshold_bps and
                        current_price > medium_ema): # Holding above medium EMA for structural integrity

                        # Ensure this is the FIRST pullback after a clear trend start
                        # This is implicitly handled by resetting self.in_pullback if structure breaks
                        # and only entering if uptrend_confirmed.

                        self.last_trade_bar = ctx.bar_index
                        self.in_pullback = False # Reset for next pullback
                        
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "structural_pullback_entry",
                                "current_price": current_price,
                                "fast_ema": fast_ema,
                                "medium_ema": medium_ema,
                                "slow_ema": slow_ema,
                                "pullback_depth_bps": round(pullback_depth_bps, 2),
                                "higher_highs": self.higher_highs,
                                "higher_lows": self.higher_lows,
                            }
                        )
            else:
                self.in_pullback = False # If not in uptrend, reset pullback state


        # Exit logic
        if ctx.has_position():
            # Exit if structure breaks (price closes below slow EMA)
            if current_price < slow_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structure_break_exit",
                        "current_price": current_price,
                        "slow_ema": slow_ema,
                    }
                )
            
            # Exit at a prior high (or close to it)
            # This is a simplified version, a more robust prior high detection might be needed
            if ctx.position["direction"] == "long" and current_price >= self.pullback_high_price * 0.995: # Close to prior high
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "reached_prior_high_exit",
                        "current_price": current_price,
                        "pullback_high_price": self.pullback_high_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass