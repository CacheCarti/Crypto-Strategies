from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergenceSOL(Strategy):
    METADATA = {
        "name": "Funding Divergence SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Aim for 3-hour average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Check funding over last 24 bars (24 hours)
        self.price_change_period = 12 # Check price change over last 12 bars (12 hours)
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2] if len(closes) >= 2 else current_price

        # Price change over the last `price_change_period` bars
        if len(closes) < self.price_change_period:
            return None
        price_change = (current_price - closes[-self.price_change_period]) / closes[-self.price_change_period]

        # Average funding rate over `funding_period`
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]
        # This is a simplification. In a real scenario, you'd want past funding rates.
        # For this contract, ctx.features.get only gives the current bar's value.
        # So we'll approximate by checking the current funding rate and its trend.
        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # To get a sense of funding trend, we need to store historical funding rates.
        # This is typically done in `self` variables, but for this exercise, we'll use a simple threshold.
        # Assume a negative funding rate means shorts are paying longs, positive means longs paying shorts.

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid trading in crisis or extreme volatility
        if crisis_score > 0.5 or regime == "CRISIS" or regime == "MELTDOWN":
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Price rising with negative funding (short squeeze potential)
        # Check for significant price rise AND significantly negative funding
        if price_change > 0.015 and current_funding_rate < -0.0001: # 1.5% rise, -0.01% funding
            if trend_regime != "bear": # Only long in non-bearish trends
                signal_direction = "long"
                confidence = min(1.0, 0.5 + abs(current_funding_rate * 1000) + price_change * 10) # Scale confidence
                reason = "sol_short_squeeze_potential"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "funding_rate_solusdt": current_funding_rate,
                    "trend_regime": trend_regime,
                    "regime": regime
                }

        # Short signal: Price falling with positive funding (long liquidation potential)
        # Check for significant price fall AND significantly positive funding
        elif price_change < -0.015 and current_funding_rate > 0.0001: # 1.5% fall, +0.01% funding
            if trend_regime != "bull": # Only short in non-bullish trends
                signal_direction = "short"
                confidence = min(1.0, 0.5 + abs(current_funding_rate * 1000) + abs(price_change) * 10) # Scale confidence
                reason = "sol_long_liquidation_potential"
                metadata = {
                    "price_change_pct": price_change * 100,
                    "funding_rate_solusdt": current_funding_rate,
                    "trend_regime": trend_regime,
                    "regime": regime
                }

        # Exit logic
        if ctx.has_position():
            if signal_direction and signal_direction != ctx.position_direction():
                # Flip position if opposite signal is strong
                self.last_trade_bar = ctx.bar_index
                metadata["exit_reason"] = f"flip_signal_{reason}"
                return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)
            
            # No strong new signal, just hold or do nothing
            return None
        
        # If no position, and we have a signal, enter
        if signal_direction and confidence > 0.6: # Require minimum confidence for entry
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, no action needed for this strategy
        pass