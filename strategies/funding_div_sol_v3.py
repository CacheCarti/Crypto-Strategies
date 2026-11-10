from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.price_period = 20  # For price momentum
        self.funding_period = 10 # For funding rate momentum
        self.cooldown_bars = 5   # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _momentum(self, values, period):
        if len(values) < period + 1:
            return None
        return values[-1] - values[-period-1]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1)
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period + 1)]

        if len(closes) < self.price_period + 1 or len(funding_rates) < self.funding_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        price_change = current_close - prev_close

        current_funding = funding_rates[-1]
        prev_funding = funding_rates[-2]
        funding_change = current_funding - prev_funding

        # Price momentum (simple 1-bar change)
        price_momentum = current_close - closes[-(self.price_period // 2) - 1] if len(closes) >= (self.price_period // 2) + 1 else 0.0

        # Funding rate momentum (simple 1-bar change)
        funding_momentum = current_funding - funding_rates[-(self.funding_period // 2) - 1] if len(funding_rates) >= (self.funding_period // 2) + 1 else 0.0


        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long condition: Price rising, but funding rate is negative (or becoming less negative)
        # Suggests shorts are trapped and could fuel a squeeze
        if price_momentum > 0 and current_funding < 0 and funding_change > -0.00001: # Funding is negative but not getting more negative
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = min(0.7, abs(price_momentum / current_close) * 100 + abs(current_funding) * 1000) # Scale confidence
                reason = "price_rising_funding_negative_squeeze_potential"
                metadata = {
                    "price_momentum": price_momentum,
                    "current_funding": current_funding,
                    "funding_change": funding_change,
                    "price": current_close
                }

        # Short condition: Price falling, but funding rate is positive (or becoming less positive)
        # Suggests longs are trapped and could fuel a downside move
        elif price_momentum < 0 and current_funding > 0 and funding_change < 0.00001: # Funding is positive but not getting more positive
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = min(0.7, abs(price_momentum / current_close) * 100 + abs(current_funding) * 1000) # Scale confidence
                reason = "price_falling_funding_positive_long_trap_potential"
                metadata = {
                    "price_momentum": price_momentum,
                    "current_funding": current_funding,
                    "funding_change": funding_change,
                    "price": current_close
                }

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        # Exit logic: if funding rate direction flips against the position
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_funding > 0 and funding_change > 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "exit_long_funding_flipped_positive",
                    "current_funding": current_funding,
                    "funding_change": funding_change,
                    "price": current_close
                })
            elif ctx.position_direction() == "short" and current_funding < 0 and funding_change < 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "exit_short_funding_flipped_negative",
                    "current_funding": current_funding,
                    "funding_change": funding_change,
                    "price": current_close
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this strategy, just logging
        pass