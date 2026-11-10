from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentExhaustionReversal(Strategy):
    METADATA = {
        "name": "Sentiment Exhaustion Reversal (ETH)",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.momentum_period = 10
        self.entry_cooldown_bars = 10
        self.last_trade_bar = -self.entry_cooldown_bars # Allow trading from start

        # Parameters for confidence scaling
        self.fgi_threshold_high = 80
        self.fgi_threshold_low = 20
        self.momentum_drop_threshold = -0.005 # Price drop over momentum_period
        self.momentum_rise_threshold = 0.005 # Price rise over momentum_period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return (closes[-1] - closes[-period]) / closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + self.momentum_period + 1)
        if len(closes) < self.long_period + self.momentum_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.entry_cooldown_bars:
            return None

        current_price = ctx.bar.close
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate momentum for the last momentum_period
        price_momentum = self._momentum(closes, self.momentum_period)
        if price_momentum is None:
            return None

        # Entry logic for short position (Sentiment Exhaustion)
        if not ctx.has_position():
            # Condition 1: High Greed
            greed_exhaustion = fear_greed_index >= self.fgi_threshold_high

            # Condition 2: Price momentum stalls or slightly reverses after a potential run-up
            # We want to catch the reversal, so we look for a slight negative momentum
            # after a period of positive momentum (implied by high FGI and market context)
            momentum_stalling = price_momentum < 0 and price_momentum > self.momentum_drop_threshold

            if greed_exhaustion and momentum_stalling:
                # Calculate confidence based on how extreme FGI is and how much momentum has stalled
                fgi_confidence = min(1.0, (fear_greed_index - self.fgi_threshold_high) / (100 - self.fgi_threshold_high))
                momentum_confidence = min(1.0, abs(price_momentum) / abs(self.momentum_drop_threshold)) # Closer to 0 is higher confidence for stalling

                confidence = (fgi_confidence * 0.6) + (momentum_confidence * 0.4) # Weighted confidence
                confidence = max(0.2, min(0.9, confidence)) # Ensure confidence is within a reasonable range

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "sentiment_exhaustion_short",
                        "fear_greed_index": fear_greed_index,
                        "price_momentum": price_momentum,
                        "current_price": current_price,
                    }
                )

        # Exit logic for existing position
        if ctx.has_position() and ctx.position_direction() == "short":
            entry_price = ctx.position["entry_price"]

            # Exit Condition 1: Fear & Greed normalizes (drops significantly)
            greed_normalized = fear_greed_index < (self.fgi_threshold_high - 10) # Drops 10 points from threshold

            # Exit Condition 2: Price momentum resumes upwards (contrary to short)
            momentum_resumed_up = price_momentum > self.momentum_rise_threshold

            # Exit Condition 3: Reached a certain profit target
            profit_target_hit = (entry_price - current_price) / entry_price >= (self.METADATA["declared_tp_bps"] / 10000.0)

            if greed_normalized or momentum_resumed_up or profit_target_hit:
                self.last_trade_bar = ctx.bar_index
                reason = ""
                if greed_normalized:
                    reason = "greed_normalized"
                elif momentum_resumed_up:
                    reason = "momentum_resumed_up"
                elif profit_target_hit:
                    reason = "profit_target_hit"

                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": f"exit_{reason}",
                        "fear_greed_index": fear_greed_index,
                        "price_momentum": price_momentum,
                        "current_price": current_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        # For this strategy, a simple cooldown is sufficient.
        pass