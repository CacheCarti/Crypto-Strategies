from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingPersistence(Strategy):
    METADATA = {
        "name": "SOL Funding Persistence Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 4 # Number of consecutive bars funding must be extreme
        self.funding_threshold = 0.00015 # 0.015% per 8h, or ~0.00001875 per hour
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # EMA for price confirmation
        self.ema_short_period = 10
        self.ema_long_period = 30


    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate EMAs for price confirmation
        ema_short_values = ctx.closes(self.ema_short_period)
        ema_long_values = ctx.closes(self.ema_long_period)

        ema_short = self._ema(ema_short_values, self.ema_short_period)
        ema_long = self._ema(ema_long_values, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Funding rate persistence check
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]
        
        # Check if we have enough historical funding rates
        if len(ctx.bar_history) < self.funding_period:
            return None

        # Get historical funding rates for the persistence check
        recent_funding_rates = []
        for i in range(self.funding_period):
            # Access funding rate from previous bars in history
            # This assumes funding_rate_solusdt is available for historical bars
            # If not, the ctx.features.get() would give the same current value repeatedly
            # For backtestability, we assume funding_rate_solusdt is updated per bar or often enough.
            # However, ctx.features.get is for the CURRENT bar. To get historical, we need to access past bars' features.
            # This is a limitation with current ctx.features.get(). For this strategy, we will assume
            # the current bar's funding rate is representative if it's persistent.
            # A more robust implementation would require a history of feature values.
            # For now, we'll check if the current funding rate has been consistently extreme.
            # This means the funding rate at ctx.bar_index, ctx.bar_index-1, ..., ctx.bar_index-self.funding_period+1
            # all satisfy the condition. The current API only gives ctx.features.get() for current bar.
            # We'll adapt by checking if the *current* funding rate is extreme and has been extreme for `funding_period` *consecutive times*
            # This implies the feature needs to be tracked across bars.
            # Given the constraint: "ctx.features.get("key", default) — platform data feeds", it provides the current bar's feature.
            # To check persistence, we need to store past feature values in self.
            # Let's use a simpler proxy: check if the *current* funding rate is extreme, and assume it's persistent enough
            # if it's extreme, for now. A true persistence check would need a feature history.

            # Re-reading the prompt, funding_rate_solusdt is "Hourly". This means it's per-bar on 1h domain.
            # So we can get the past values.
            funding_rate_at_bar = ctx.bar_history[-1-i].features.get("funding_rate_solusdt", 0.0)
            recent_funding_rates.append(funding_rate_at_bar)

        # Reverse to get oldest first
        recent_funding_rates.reverse()

        is_funding_extremely_positive = all(fr > self.funding_threshold for fr in recent_funding_rates)
        is_funding_extremely_negative = all(fr < -self.funding_threshold for fr in recent_funding_rates)

        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        metadata = {}

        if ctx.has_position():
            # If holding a position, check for exit conditions or just hold
            if ctx.position_direction() == "long" and is_funding_extremely_positive and current_price < ema_short:
                # If funding turns even more positive AND price drops below short EMA, consider exiting long
                signal_direction = "flat"
                reason = "long_exit_funding_positive_and_price_below_ema"
                metadata = {"current_funding": recent_funding_rates[-1], "ema_short": ema_short, "price": current_price}
            elif ctx.position_direction() == "short" and is_funding_extremely_negative and current_price > ema_short:
                # If funding turns even more negative AND price rises above short EMA, consider exiting short
                signal_direction = "flat"
                reason = "short_exit_funding_negative_and_price_above_ema"
                metadata = {"current_funding": recent_funding_rates[-1], "ema_short": ema_short, "price": current_price}
            else:
                return ctx.signal("hold") # Hold existing position
        
        # Entry logic
        if signal_direction is None: # Only consider new entries if no exit signal was generated or no position
            # Fade extremely positive funding (go short)
            if is_funding_extremely_positive and current_price > ema_long and ema_short > ema_long:
                # Funding is extremely positive (people paying to be long), price is in an uptrend (above long EMA),
                # but short EMA is above long EMA (confirming strength). Fade this.
                signal_direction = "short"
                confidence = 0.65 # Moderate confidence
                reason = "fade_extreme_positive_funding_uptrend_confirmation"
                metadata = {
                    "funding_rates": recent_funding_rates,
                    "funding_threshold": self.funding_threshold,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "price": current_price
                }

            # Fade extremely negative funding (go long)
            elif is_funding_extremely_negative and current_price < ema_long and ema_short < ema_long:
                # Funding is extremely negative (people paying to be short), price is in a downtrend (below long EMA),
                # but short EMA is below long EMA (confirming weakness). Fade this.
                signal_direction = "long"
                confidence = 0.65 # Moderate confidence
                reason = "fade_extreme_negative_funding_downtrend_confirmation"
                metadata = {
                    "funding_rates": recent_funding_rates,
                    "funding_threshold": self.funding_threshold,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "price": current_price
                }

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, simply log.
        pass