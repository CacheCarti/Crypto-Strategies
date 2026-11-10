from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipUnwind(Strategy):
    METADATA = {
        "name": "Funding Rate Flip Unwind",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # ~3 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 12 # Lookback for stable funding
        self.confirmation_bars = 3 # How many bars to confirm price movement
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.funding_period + self.confirmation_bars)
        if len(closes) < self.funding_period + self.confirmation_bars:
            return None

        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        previous_fundings = [ctx.features.get("funding_rate_solusdt", 0.0, offset=i) for i in range(1, self.funding_period + 1)]

        if ctx.has_position():
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        signal_direction = None
        metadata = {"reason": "no_signal"}

        # Check for a flip from negative to positive (long signal)
        if current_funding > 0 and all(f < 0 for f in previous_fundings):
            # Funding flipped from consistently negative to positive
            # Check if price confirmed the upward move
            price_change = ctx.bar.close - ctx.closes(self.confirmation_bars + 1)[0]
            if price_change > 0: # Price moved up
                signal_direction = "long"
                metadata = {
                    "reason": "funding_flip_neg_to_pos_price_confirm",
                    "current_funding": current_funding,
                    "previous_fundings_avg": sum(previous_fundings) / len(previous_fundings),
                    "price_change_conf_bars": price_change
                }

        # Check for a flip from positive to negative (short signal)
        elif current_funding < 0 and all(f > 0 for f in previous_fundings):
            # Funding flipped from consistently positive to negative
            # Check if price confirmed the downward move
            price_change = ctx.bar.close - ctx.closes(self.confirmation_bars + 1)[0]
            if price_change < 0: # Price moved down
                signal_direction = "short"
                metadata = {
                    "reason": "funding_flip_pos_to_neg_price_confirm",
                    "current_funding": current_funding,
                    "previous_fundings_avg": sum(previous_fundings) / len(previous_fundings),
                    "price_change_conf_bars": price_change
                }

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            # Confidence based on magnitude of funding flip and price confirmation
            confidence = min(0.5 + abs(current_funding) * 100, 0.9) # Scale funding rate to confidence
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log closing reason or adapt strategy parameters
        pass