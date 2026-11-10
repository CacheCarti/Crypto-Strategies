from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional
import math

class SolFundingDivergence(Strategy):
    METADATA = {
        "name": "SOL Funding Divergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # 24 hours for funding rate trend
        self.price_period = 12 # 12 hours for price trend
        self.funding_threshold = -0.00005 # Negative funding below this is significant
        self.positive_funding_threshold = 0.00005 # Positive funding above this is significant
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_period + 1)
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period + 1)] # Get current and historical funding

        if len(closes) < self.price_period + 1 or len(funding_rates) < self.funding_period + 1:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2] if len(closes) >= 2 else current_price
        
        price_sma = self._sma(closes, self.price_period)
        if price_sma is None:
            return None

        current_funding = funding_rates[-1]
        
        # Calculate funding rate trend (simple average of recent funding)
        avg_funding_rate_recent = sum(funding_rates[-self.funding_period:]) / self.funding_period

        signal_confidence = 0.6
        
        # Long condition: Price rising, but funding is significantly negative (potential short squeeze)
        # Price must be above its short-term SMA to confirm uptrend strength
        if (current_price > prev_price * 1.002 and  # Price rising
            current_price > price_sma and # Price confirms uptrend
            current_funding < self.funding_threshold and # Current funding is very negative
            avg_funding_rate_recent < self.funding_threshold * 0.5): # Recent funding trend is also negative
            
            if not ctx.has_position() or ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "sol_funding_divergence_long",
                        "current_price": current_price,
                        "price_sma": price_sma,
                        "current_funding": current_funding,
                        "avg_funding_rate_recent": avg_funding_rate_recent,
                    }
                )

        # Short condition: Price falling, but funding is significantly positive (potential long liquidation)
        # Price must be below its short-term SMA to confirm downtrend strength
        if (current_price < prev_price * 0.998 and # Price falling
            current_price < price_sma and # Price confirms downtrend
            current_funding > self.positive_funding_threshold and # Current funding is very positive
            avg_funding_rate_recent > self.positive_funding_threshold * 0.5): # Recent funding trend is also positive
            
            if not ctx.has_position() or ctx.position_direction() == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "sol_funding_divergence_short",
                        "current_price": current_price,
                        "price_sma": price_sma,
                        "current_funding": current_funding,
                        "avg_funding_rate_recent": avg_funding_rate_recent,
                    }
                )

        # Exit logic: if funding reverses significantly while in position
        if ctx.has_position():
            pos = ctx.position
            if pos["direction"] == "long":
                # Exit long if funding turns positive or price breaks below SMA
                if current_funding > 0.0 or current_price < price_sma * 0.995:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_long_funding_reversal_or_price_break",
                            "current_funding": current_funding,
                            "current_price": current_price,
                            "price_sma": price_sma,
                        }
                    )
            elif pos["direction"] == "short":
                # Exit short if funding turns negative or price breaks above SMA
                if current_funding < 0.0 or current_price > price_sma * 1.005:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "exit_short_funding_reversal_or_price_break",
                            "current_funding": current_funding,
                            "current_price": current_price,
                            "price_sma": price_sma,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: dict) -> None:
        # No adaptive logic for this strategy
        pass