from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipETH(Strategy):
    METADATA = {
        "name": "ETH Funding Flip",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_lookback = 3
        self.price_confirm_period = 3
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        previous_fundings = [ctx.features.get("funding_rate_ethusdt", 0.0, offset=i) for i in range(1, self.funding_lookback + 1)]

        # Check for funding rate flip from negative to positive (long signal)
        # We want to see a clear flip, not just a momentary positive
        # Condition: previous funding rates were negative, and current is clearly positive
        is_long_funding_flip = all(f < -0.00005 for f in previous_fundings) and current_funding > 0.00005

        # Check for funding rate flip from positive to negative (short signal)
        # Condition: previous funding rates were positive, and current is clearly negative
        is_short_funding_flip = all(f > 0.00005 for f in previous_fundings) and current_funding < -0.00005

        # Price confirmation: current close must be above/below a short-term SMA
        # This helps confirm the funding sentiment is aligning with price action
        sma_confirm = self._sma(closes, self.price_confirm_period)
        if sma_confirm is None:
            return None
        
        long_price_confirm = ctx.bar.close > sma_confirm
        short_price_confirm = ctx.bar.close < sma_confirm

        if not ctx.has_position():
            if is_long_funding_flip and long_price_confirm:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_long_price_confirm",
                        "current_funding": current_funding,
                        "prev_fundings": previous_fundings,
                        "close": ctx.bar.close,
                        "sma_confirm": sma_confirm,
                    }
                )
            elif is_short_funding_flip and short_price_confirm:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_short_price_confirm",
                        "current_funding": current_funding,
                        "prev_fundings": previous_fundings,
                        "close": ctx.bar.close,
                        "sma_confirm": sma_confirm,
                    }
                )
        else:
            # If in position, hold unless a strong opposing signal appears
            # For this strategy, we'll let the SL/TP handle exits, or a clear flip to opposite direction
            if ctx.position_direction() == "long" and is_short_funding_flip and short_price_confirm:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "funding_flip_opposing_signal_exit",
                        "current_funding": current_funding,
                        "prev_fundings": previous_fundings,
                        "close": ctx.bar.close,
                        "sma_confirm": sma_confirm,
                    }
                )
            elif ctx.position_direction() == "short" and is_long_funding_flip and long_price_confirm:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "funding_flip_opposing_signal_exit",
                        "current_funding": current_funding,
                        "prev_fundings": previous_fundings,
                        "close": ctx.bar.close,
                        "sma_confirm": sma_confirm,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        pass