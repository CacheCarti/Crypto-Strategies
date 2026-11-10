from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPersistenceFader(Strategy):
    METADATA = {
        "name": "FundingPersistenceFader",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 6 # Check persistence over 6 hours
        self.funding_threshold = 0.00015 # 0.015% per 8h, or 0.00015 per hour
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_long_period + 1)
        if len(closes) < self.ema_long_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        recent_fundings = [ctx.features.get(f"funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # This will get actual recent values, not just the current one repeated
        # The engine provides historical feature values if requested like this.
        # But a safer approach is to track history in initialize if needed.
        # For simplicity, we'll assume ctx.features.get("key") gets the value for current bar.
        # To get historical funding, we would need to store it or assume ctx.funding_rates(n) existed.
        # Since it doesn't, we'll check persistence on current and recent past values.
        # A more robust way to check persistence would be to store funding rates in `self` across bars.
        # For this example, we'll simplify and check if the current funding is extreme and has been for a few bars.

        # To properly check persistence, we need to maintain a history of funding rates.
        # This is a common pattern for "advanced" strategies.
        if not hasattr(self, 'funding_history'):
            self.funding_history = []
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.funding_period:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.funding_period:
            return None

        # Calculate EMAs
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        if ema_short is None or ema_long is None:
            return None

        # Regime filters
        regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Funding persistence check
        persistent_high_funding = all(f > self.funding_threshold for f in self.funding_history)
        persistent_low_funding = all(f < -self.funding_threshold for f in self.funding_history)

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Persistent negative funding (people are paying to short) -> market likely oversold or capitulating
        # Fade the shorts
        if persistent_low_funding:
            # Price confirmation: price is below short EMA, indicating weakness, but we are looking for a reversal
            # Or, price is trying to recover (short EMA crossing above long EMA)
            if ctx.bar.close < ema_short and ema_short < ema_long: # Price is weak, but could be bottoming
                # Avoid trading into strong downtrends or crisis
                if regime != "bear" and crisis_score < 0.3:
                    signal_direction = "long"
                    confidence = 0.6 + ctx.rng.uniform(0, 0.2) # Add some randomness
                    reason = "persistent_negative_funding_fade_weakness"
                    metadata = {
                        "funding_history": [round(f, 6) for f in self.funding_history],
                        "ema_short": round(ema_short, 2),
                        "ema_long": round(ema_long, 2),
                        "close": round(ctx.bar.close, 2),
                        "regime": regime,
                        "crisis_score": round(crisis_score, 2),
                    }

        # Short signal: Persistent positive funding (people are paying to long) -> market likely overbought or topping
        # Fade the longs
        elif persistent_high_funding:
            # Price confirmation: price is above short EMA, indicating strength, but we are looking for a reversal
            # Or, price is trying to roll over (short EMA crossing below long EMA)
            if ctx.bar.close > ema_short and ema_short > ema_long: # Price is strong, but could be topping
                # Avoid trading into strong uptrends or crisis
                if regime != "bull" and crisis_score < 0.3:
                    signal_direction = "short"
                    confidence = 0.6 + ctx.rng.uniform(0, 0.2)
                    reason = "persistent_positive_funding_fade_strength"
                    metadata = {
                        "funding_history": [round(f, 6) for f in self.funding_history],
                        "ema_short": round(ema_short, 2),
                        "ema_long": round(ema_long, 2),
                        "close": round(ctx.bar.close, 2),
                        "regime": regime,
                        "crisis_score": round(crisis_score, 2),
                    }
        
        # Exit logic
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            if pos_direction == "long":
                # If funding turns positive or price breaks down significantly
                if current_funding > 0.00005 or ctx.bar.close < ema_long:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_exit_funding_reversal_or_ema_breakdown", "current_funding": round(current_funding, 6), "ema_long": round(ema_long, 2)})
            elif pos_direction == "short":
                # If funding turns negative or price breaks up significantly
                if current_funding < -0.00005 or ctx.bar.close > ema_long:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_exit_funding_reversal_or_ema_breakup", "current_funding": round(current_funding, 6), "ema_long": round(ema_long, 2)})

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
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state if necessary, or log performance
        pass