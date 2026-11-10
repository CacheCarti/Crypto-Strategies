from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPersistenceFade(Strategy):
    METADATA = {
        "name": "Funding Persistence Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hours hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 10 # Number of bars to check for persistent funding
        self.funding_threshold = 0.0002 # 0.02% funding rate (per 8h)
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period = 20 # For price trend filter

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.ema_period)
        if len(closes) < self.ema_period:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        funding_rates = [ctx.features.get("funding_rate_ethusdt", 0.0) for _ in range(self.funding_period)]
        
        # Check for persistent positive funding (fade with short)
        if all(fr > self.funding_threshold for fr in funding_rates) and len(funding_rates) == self.funding_period:
            if not ctx.has_position():
                # Add a price filter: only short if price is below EMA (fading into weakness)
                if current_price < ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "persistent_high_funding_fade_below_ema",
                            "funding_rate_avg": sum(funding_rates) / self.funding_period,
                            "funding_threshold": self.funding_threshold,
                            "current_price": current_price,
                            "ema_20": ema
                        }
                    )
            elif ctx.position_direction() == "long":
                # If we are long and funding flips to persistently high, consider flipping
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8, # Higher confidence for a flip
                    metadata={
                        "reason": "flip_to_short_on_persistent_high_funding_below_ema",
                        "funding_rate_avg": sum(funding_rates) / self.funding_period,
                        "funding_threshold": self.funding_threshold,
                        "current_price": current_price,
                        "ema_20": ema
                    }
                )

        # Check for persistent negative funding (fade with long)
        elif all(fr < -self.funding_threshold for fr in funding_rates) and len(funding_rates) == self.funding_period:
            if not ctx.has_position():
                # Add a price filter: only long if price is above EMA (fading into strength)
                if current_price > ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "persistent_low_funding_fade_above_ema",
                            "funding_rate_avg": sum(funding_rates) / self.funding_period,
                            "funding_threshold": self.funding_threshold,
                            "current_price": current_price,
                            "ema_20": ema
                        }
                    )
            elif ctx.position_direction() == "short":
                # If we are short and funding flips to persistently low, consider flipping
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8, # Higher confidence for a flip
                    metadata={
                        "reason": "flip_to_long_on_persistent_low_funding_above_ema",
                        "funding_rate_avg": sum(funding_rates) / self.funding_period,
                        "funding_threshold": self.funding_threshold,
                        "current_price": current_price,
                        "ema_20": ema
                    }
                )

        # Exit condition: funding normalizes
        if ctx.has_position():
            current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
            if abs(current_funding) < self.funding_threshold / 2: # Exit if funding is significantly normalized
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized",
                        "current_funding_rate": current_funding,
                        "exit_threshold": self.funding_threshold / 2
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, no action needed for this strategy
        pass