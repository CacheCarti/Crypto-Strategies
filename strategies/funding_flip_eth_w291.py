from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipConfirmation(Strategy):
    METADATA = {
        "name": "Funding Flip Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour holds on average
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24 bars for funding rate check (24 hours)
        self.price_confirm_period = 3 # Bars to confirm price direction
        self.ema_period = 20 # EMA for price confirmation
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_signal_bar = -self.cooldown_bars

        # State to track funding rate changes
        self.last_funding_rate = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial EMA as SMA
        ema = sum(values[-period:]) / period
        # This is a simplification for a single EMA value, not a series
        # For a true EMA series, we'd need to compute it iteratively
        # For our use case, we just need the latest EMA
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.funding_period + self.ema_period + self.price_confirm_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        # We need at least two funding rate values to detect a flip
        if self.last_funding_rate == 0.0:
            self.last_funding_rate = current_funding_rate
            return None

        # Check for funding rate flip
        funding_flip_long = (self.last_funding_rate < 0 and current_funding_rate >= 0)
        funding_flip_short = (self.last_funding_rate > 0 and current_funding_rate <= 0)

        signal = None
        confidence = 0.0
        reason = ""
        
        # Price confirmation using EMA
        current_ema = self._ema(closes, self.ema_period)
        if current_ema is None:
            self.last_funding_rate = current_funding_rate
            return None

        # Long signal logic
        if funding_flip_long and not ctx.has_position():
            # Check for price confirmation: current close above EMA and recent closes showing upward momentum
            price_confirms_long = all(c > current_ema for c in closes[-self.price_confirm_period:]) and \
                                  closes[-1] > closes[-self.price_confirm_period]
            
            if price_confirms_long:
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add a touch of randomness
                signal = "long"
                reason = "funding_flip_to_positive_price_above_ema"
        
        # Short signal logic
        elif funding_flip_short and not ctx.has_position():
            # Check for price confirmation: current close below EMA and recent closes showing downward momentum
            price_confirms_short = all(c < current_ema for c in closes[-self.price_confirm_period:]) and \
                                   closes[-1] < closes[-self.price_confirm_period]
            
            if price_confirms_short:
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1)
                signal = "short"
                reason = "funding_flip_to_negative_price_below_ema"

        # Exit logic
        if ctx.has_position():
            current_direction = ctx.position_direction()
            
            # If funding flips against our position, or price crosses EMA against us, consider exiting
            if (current_direction == "long" and (funding_flip_short or closes[-1] < current_ema)) or \
               (current_direction == "short" and (funding_flip_long or closes[-1] > current_ema)):
                
                signal = "flat"
                confidence = 0.6
                reason = "funding_flip_or_ema_cross_against_position"
                
                # Check for regime for potential adaptive SL/TP or confidence
                if ctx.market.get("regime", "NORMAL") == "HIGH_VOL":
                    confidence += 0.1 # More confident to exit in high vol
                
                self.last_signal_bar = ctx.bar_index # Cooldown after exit
                
                return ctx.signal(
                    signal, 
                    confidence=confidence, 
                    metadata={
                        "reason": reason,
                        "current_funding_rate": current_funding_rate,
                        "last_funding_rate": self.last_funding_rate,
                        "current_close": closes[-1],
                        "current_ema": current_ema,
                        "regime": ctx.market.get("regime", "NORMAL")
                    }
                )

        # Update last funding rate for the next bar
        self.last_funding_rate = current_funding_rate

        if signal:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_funding_rate": current_funding_rate,
                    "last_funding_rate": self.last_funding_rate,
                    "current_close": closes[-1],
                    "current_ema": current_ema,
                    "regime": ctx.market.get("regime", "NORMAL")
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position close, regardless of signal or external stop
        self.last_signal_bar = ctx.bar_index
        # Optional: Adaptive logic based on position outcome
        # if position["return_bps"] > 0:
        #     self.cooldown_bars = max(6, self.cooldown_bars - 1) # Reduce cooldown after a win
        # else:
        #     self.cooldown_bars = min(24, self.cooldown_bars + 1) # Increase cooldown after a loss
        pass