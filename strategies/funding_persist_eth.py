from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateFadeStrategy(Strategy):
    METADATA = {
        "name": "Funding Rate Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 6 # Check funding over last 6 bars (6 hours)
        self.funding_threshold = 0.00015 # 0.015% per 8 hours, considered extreme
        self.cooldown_bars = 12 # Cooldown for 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider trades if no position is currently open
        if ctx.has_position():
            # If funding rate normalizes, consider exiting
            current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
            if ctx.position_direction() == "long" and current_funding < self.funding_threshold * 0.5:
                # Funding became less negative, exit long
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long_exit", "current_funding": current_funding})
            elif ctx.position_direction() == "short" and current_funding > -self.funding_threshold * 0.5:
                # Funding became less positive, exit short
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short_exit", "current_funding": current_funding})
            return None

        closes = ctx.closes(self.funding_period + 1)
        if len(closes) < self.funding_period + 1:
            return None

        funding_rates = [ctx.features.get(f"funding_rate_ethusdt", 0.0) for _ in range(self.funding_period)]

        # Check for persistent extreme negative funding (fade to long)
        extreme_negative_funding_count = 0
        for rate in funding_rates:
            if rate < -self.funding_threshold:
                extreme_negative_funding_count += 1

        if extreme_negative_funding_count >= self.funding_period * 0.8: # 80% of bars must be extreme
            # Entry confirmation: Current close must be above previous close
            if ctx.bar.close > closes[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "persistent_extreme_negative_funding_fade_long",
                        "funding_rates_avg": sum(funding_rates) / len(funding_rates),
                        "funding_threshold": self.funding_threshold,
                        "close_confirm": ctx.bar.close,
                        "prev_close": closes[-2],
                    }
                )

        # Check for persistent extreme positive funding (fade to short)
        extreme_positive_funding_count = 0
        for rate in funding_rates:
            if rate > self.funding_threshold:
                extreme_positive_funding_count += 1

        if extreme_positive_funding_count >= self.funding_period * 0.8: # 80% of bars must be extreme
            # Entry confirmation: Current close must be below previous close
            if ctx.bar.close < closes[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "persistent_extreme_positive_funding_fade_short",
                        "funding_rates_avg": sum(funding_rates) / len(funding_rates),
                        "funding_threshold": self.funding_threshold,
                        "close_confirm": ctx.bar.close,
                        "prev_close": closes[-2],
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown is applied after any close
        self.last_trade_bar = ctx.bar_index