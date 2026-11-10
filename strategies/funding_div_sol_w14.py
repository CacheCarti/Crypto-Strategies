from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SqueezeDivergenceFunding(Strategy):
    METADATA = {
        "name": "Squeeze Divergence Funding",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.price_period = 20
        self.funding_period = 10
        self.funding_threshold = -0.0001  # Negative funding to consider for long
        self.funding_pos_threshold = 0.0001 # Positive funding to consider for short
        self.min_price_change_pct = 0.005 # 0.5% price change
        self.cooldown_bars = 5 # Cooldown after a trade

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
    
    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1) # Need one extra for price change
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]

        if len(closes) < self.price_period + 1 or len(funding_rates) < self.funding_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_solusdt", 0.0)
        
        # Price change over the period
        price_start = closes[-(self.price_period + 1)]
        price_end = closes[-1]
        price_change_pct = (price_end - price_start) / price_start

        # Average funding rate over the period
        avg_funding_rate = sum(funding_rates) / self.funding_period

        signal_confidence = 0.0
        signal_direction = "hold"
        signal_reason = ""
        signal_metadata = {
            "current_price": price_end,
            "price_change_pct": price_change_pct,
            "current_funding_rate": current_funding,
            "avg_funding_rate": avg_funding_rate,
        }

        # Long condition: Price rises significantly while funding is consistently negative
        # Implies shorts are trapped and could be squeezed, fueling further upside.
        if (price_change_pct > self.min_price_change_pct and 
            current_funding < self.funding_threshold and
            avg_funding_rate < 0):
            
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                signal_confidence = min(0.6 + abs(price_change_pct * 10) + abs(avg_funding_rate * 10000), 0.9)
                signal_reason = "price_rise_negative_funding_squeeze_divergence"
                signal_metadata["price_change_threshold"] = self.min_price_change_pct
                signal_metadata["funding_threshold"] = self.funding_threshold

        # Short condition: Price falls significantly while funding is consistently positive
        # Implies longs are trapped and could be liquidated, fueling further downside.
        elif (price_change_pct < -self.min_price_change_pct and
              current_funding > self.funding_pos_threshold and
              avg_funding_rate > 0):
            
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                signal_confidence = min(0.6 + abs(price_change_pct * 10) + abs(avg_funding_rate * 10000), 0.9)
                signal_reason = "price_fall_positive_funding_liquidation_divergence"
                signal_metadata["price_change_threshold"] = -self.min_price_change_pct
                signal_metadata["funding_pos_threshold"] = self.funding_pos_threshold
        
        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long" and price_change_pct < -self.min_price_change_pct / 2: # Small reversal against
                signal_direction = "flat"
                signal_confidence = 0.5
                signal_reason = "long_exit_price_reversal"
            elif ctx.position_direction() == "short" and price_change_pct > self.min_price_change_pct / 2: # Small reversal against
                signal_direction = "flat"
                signal_confidence = 0.5
                signal_reason = "short_exit_price_reversal"


        if signal_direction != "hold" and signal_confidence > 0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata=signal_metadata
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass