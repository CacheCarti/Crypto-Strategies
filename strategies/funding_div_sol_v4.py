from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "FundingSqueezeDivergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough bars for 20-period SMA and funding rate history
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.sma_period = 20
        self.funding_period = 10 # Period to check for funding rate trend
        self.min_funding_rate_threshold = -0.00015 # e.g. -0.015% per 8h
        self.max_funding_rate_threshold = 0.00015  # e.g. 0.015% per 8h
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.sma_period + 1) # Need one more for price change calc
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0)]
        # Add past funding rates if available, for trend detection
        # Note: ctx.features.get("funding_rate_solusdt") only gives current bar's.
        # For a trend, we'd need historical funding rates, which isn't directly exposed
        # in ctx.features(n). Assuming funding_rate_solusdt is available for current bar only,
        # we can only check its current value. If historical was available, we'd use it.

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2]
        price_change = current_price - prev_price
        
        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Avoid trading during extreme volatility or strong opposing trends
        if market_regime == "CRISIS" or market_regime == "MELTDOWN":
            return None
        
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long condition: Price rising, but funding rate is significantly negative (trapped shorts)
        if price_change > 0 and current_funding_rate < self.min_funding_rate_threshold:
            # Add a trend filter for higher confidence
            if trend_regime == "bull" or trend_regime == "neutral":
                signal_direction = "long"
                reason = "funding_squeeze_long_divergence"
                confidence = 0.7 + (ctx.market.get("trend_regime_confidence", 0.0) / 2) # Higher confidence with trend alignment
        
        # Short condition: Price falling, but funding rate is significantly positive (trapped longs)
        elif price_change < 0 and current_funding_rate > self.max_funding_rate_threshold:
            # Add a trend filter for higher confidence
            if trend_regime == "bear" or trend_regime == "neutral":
                signal_direction = "short"
                reason = "funding_squeeze_short_divergence"
                confidence = 0.7 + (ctx.market.get("trend_regime_confidence", 0.0) / 2) # Higher confidence with trend alignment

        # Exit logic
        if ctx.has_position():
            if signal_direction == "long" and ctx.position_direction() == "short":
                # Flip from short to long
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": f"flip_short_to_{reason}",
                    "current_price": current_price,
                    "price_change": price_change,
                    "funding_rate": current_funding_rate,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime
                })
            elif signal_direction == "short" and ctx.position_direction() == "long":
                # Flip from long to short
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": f"flip_long_to_{reason}",
                    "current_price": current_price,
                    "price_change": price_change,
                    "funding_rate": current_funding_rate,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime
                })
            # Consider exiting if divergence reverses or conditions no longer hold strongly
            # For simplicity, we'll let SL/TP manage exits for now, or a flip signal
            return None # Hold current position

        # Entry logic
        if signal_direction and confidence > 0.6: # Only enter if confidence is sufficient
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata={
                "reason": reason,
                "current_price": current_price,
                "price_change": price_change,
                "funding_rate": current_funding_rate,
                "market_regime": market_regime,
                "trend_regime": trend_regime
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis, or adapt strategy parameters
        # For this strategy, we just ensure cooldown is reset.
        pass