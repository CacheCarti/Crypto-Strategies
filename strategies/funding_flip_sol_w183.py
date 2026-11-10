from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipReversion(Strategy):
    METADATA = {
        "name": "Funding Flip Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 8  # Lookback for stable funding
        self.price_confirm_bars = 3 # Bars to confirm price action after flip
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Threshold for what constitutes a "stable" funding rate
        self.stable_funding_threshold = 0.000005 # 0.0005%

        # Price confirmation thresholds
        self.long_confirm_pct = 0.002 # 0.2% price increase
        self.short_confirm_pct = 0.002 # 0.2% price decrease

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.funding_period + self.price_confirm_bars + 1)
        highs = ctx.highs(self.funding_period + self.price_confirm_bars + 1)
        lows = ctx.lows(self.funding_period + self.price_confirm_bars + 1)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period + 1)]
        # Fetch funding rates for the past N bars
        # Note: funding_rate_solusdt is hourly, so simple ctx.features.get() will give current bar's rate.
        # To get historical funding rates, we would need a list of past feature values, which is not directly exposed in BarContext.
        # For simplicity and backtestability, we will assume the funding rate is evaluated for the current bar only,
        # but the "stable readings" part of the prompt implies historical evaluation.
        # A proxy for "stable readings" is to check if the current funding rate is within a tight range of recent price action,
        # or if it has been consistently positive/negative.
        # Given the BarContext API, we can only access the current bar's funding rate directly.
        # We'll adapt: "several stable readings" means the funding rate has been consistently positive/negative for a few bars *before* the flip.
        # This requires storing past funding rates, which we can do in `self`.

        # Store funding rates history (limited size)
        if not hasattr(self, 'funding_history'):
            self.funding_history = []
        self.funding_history.append(ctx.features.get("funding_rate_solusdt", 0.0))
        if len(self.funding_history) > self.funding_period + 2:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.funding_period + 1:
            return None

        current_funding = self.funding_history[-1]
        previous_funding = self.funding_history[-2]
        
        # Check for stable readings before the flip
        # We need funding_period + 1 values to check for a flip after a stable period.
        # funding_history[-self.funding_period-1:-1] gives the values *before* the previous funding.
        recent_stable_funding = self.funding_history[-self.funding_period-1:-1]
        
        is_stable_positive = all(f > self.stable_funding_threshold for f in recent_stable_funding)
        is_stable_negative = all(f < -self.stable_funding_threshold for f in recent_stable_funding)

        signal_direction = None
        reason = ""
        metadata_values = {
            "current_funding": current_funding,
            "previous_funding": previous_funding,
            "stable_funding_threshold": self.stable_funding_threshold
        }

        # Long signal: funding flips from negative (stable) to positive
        if is_stable_negative and previous_funding < -self.stable_funding_threshold and current_funding > self.stable_funding_threshold:
            # Check price confirmation: price should have risen in the last few bars
            # Compare current close to the close at the start of the confirmation window
            confirm_start_price = closes[-self.price_confirm_bars - 1]
            price_change = (closes[-1] - confirm_start_price) / confirm_start_price
            if price_change > self.long_confirm_pct:
                signal_direction = "long"
                reason = "funding_flip_neg_to_pos_price_confirm"
                metadata_values["price_change_pct"] = price_change
                metadata_values["confirm_threshold"] = self.long_confirm_pct

        # Short signal: funding flips from positive (stable) to negative
        elif is_stable_positive and previous_funding > self.stable_funding_threshold and current_funding < -self.stable_funding_threshold:
            # Check price confirmation: price should have fallen in the last few bars
            confirm_start_price = closes[-self.price_confirm_bars - 1]
            price_change = (closes[-1] - confirm_start_price) / confirm_start_price
            if price_change < -self.short_confirm_pct:
                signal_direction = "short"
                reason = "funding_flip_pos_to_neg_price_confirm"
                metadata_values["price_change_pct"] = price_change
                metadata_values["confirm_threshold"] = -self.short_confirm_pct

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.7,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": reason, **metadata_values}
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: could adjust confidence or cooldown based on recent performance
        pass