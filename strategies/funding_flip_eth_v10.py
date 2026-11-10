from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipETH(Strategy):
    METADATA = {
        "name": "Funding Flip ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 2  # Lookback for funding change detection (e.g., 2 bars)
        self.price_confirm_period = 3 # Bars to confirm price direction
        self.cooldown_bars = 5   # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        # For simple EMA for price confirmation
        self.ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + self.funding_period + self.price_confirm_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        previous_fundings = [ctx.features.get("funding_rate_ethusdt", 0.0, offset=-i) for i in range(1, self.funding_period + 1)]

        # Calculate EMA for price confirmation
        current_ema = self._ema(closes, self.ema_period)
        if current_ema is None:
            return None

        signal_confidence = 0.6 # Base confidence

        # Long signal logic
        # Funding flips from negative to positive
        if current_funding > 0 and all(f <= 0 for f in previous_fundings):
            # Price confirms with a positive close above EMA for the last few bars
            is_price_confirm = True
            for i in range(self.price_confirm_period):
                if closes[-1-i] < current_ema: # Check if recent closes are above EMA
                    is_price_confirm = False
                    break
            
            if is_price_confirm and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "funding_flip_long_price_confirm",
                        "current_funding": current_funding,
                        "previous_fundings": previous_fundings,
                        "current_close": ctx.bar.close,
                        "current_ema": current_ema,
                        "price_confirm_bars": self.price_confirm_period,
                    }
                )
            
        # Short signal logic
        # Funding flips from positive to negative
        elif current_funding < 0 and all(f >= 0 for f in previous_fundings):
            # Price confirms with a negative close below EMA for the last few bars
            is_price_confirm = True
            for i in range(self.price_confirm_period):
                if closes[-1-i] > current_ema: # Check if recent closes are below EMA
                    is_price_confirm = False
                    break
            
            if is_price_confirm and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "funding_flip_short_price_confirm",
                        "current_funding": current_funding,
                        "previous_fundings": previous_fundings,
                        "current_close": ctx.bar.close,
                        "current_ema": current_ema,
                        "price_confirm_bars": self.price_confirm_period,
                    }
                )

        # If we have a position, check for exit conditions based on price losing momentum
        if ctx.has_position():
            # If price moves against the position significantly, consider exiting to re-evaluate
            # For simplicity, let's say if current close moves past EMA in the wrong direction
            current_close = ctx.bar.close
            if ctx.position_direction() == "long" and current_close < current_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_exit_ema_cross_down",
                        "current_close": current_close,
                        "current_ema": current_ema,
                        "entry_price": ctx.position["entry_price"]
                    }
                )
            elif ctx.position_direction() == "short" and current_close > current_ema:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_exit_ema_cross_up",
                        "current_close": current_close,
                        "current_ema": current_ema,
                        "entry_price": ctx.position["entry_price"]
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enforce cooldown, regardless of how the position closed
        self.last_trade_bar_index = ctx.bar_index
        # Optional: Log position details
        # ctx.log(f"Position closed: {position['direction']} return {position['return_bps']} bps after {position['bars_held']} bars.")