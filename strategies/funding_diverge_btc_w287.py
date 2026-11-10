from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # 24 hours for funding rate average
        self.price_ema_fast_period = 5
        self.price_ema_slow_period = 20
        self.funding_threshold = -0.00015 # Negative funding rate threshold for long
        self.funding_positive_threshold = 0.00015 # Positive funding rate threshold for short
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

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
        closes = ctx.closes(self.price_ema_slow_period + self.cooldown_bars)
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]

        if len(closes) < self.price_ema_slow_period + 1 or len(funding_rates) < self.funding_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.price_ema_fast_period)
        slow_ema = self._ema(closes, self.price_ema_slow_period)
        
        # Funding rate average (simple average for the last `funding_period` hours)
        avg_funding_rate = sum(funding_rates) / len(funding_rates)

        if ctx.has_position():
            # Check for position exit conditions (e.g., if conditions reverse strongly)
            if ctx.position_direction() == "long":
                # If funding turns significantly positive while we are long, consider exiting
                if avg_funding_rate > self.funding_positive_threshold * 0.5: # Half threshold to exit
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_exit_funding_reversal", "avg_funding": avg_funding_rate, "current_price": current_price})
            elif ctx.position_direction() == "short":
                # If funding turns significantly negative while we are short, consider exiting
                if avg_funding_rate < self.funding_threshold * 0.5: # Half threshold to exit
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_exit_funding_reversal", "avg_funding": avg_funding_rate, "current_price": current_price})
            return None # Hold position if no exit condition met

        # Long Entry: Price rising (fast EMA > slow EMA) AND significantly negative funding
        if fast_ema is not None and slow_ema is not None and fast_ema > slow_ema and avg_funding_rate < self.funding_threshold:
            # Additional confirmation: price is above the slow EMA
            if current_price > slow_ema:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_divergence_long_squeeze",
                        "avg_funding_rate": avg_funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price,
                    }
                )

        # Short Entry: Price falling (fast EMA < slow EMA) AND significantly positive funding
        if fast_ema is not None and slow_ema is not None and fast_ema < slow_ema and avg_funding_rate > self.funding_positive_threshold:
            # Additional confirmation: price is below the slow EMA
            if current_price < slow_ema:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_divergence_short_liquidation",
                        "avg_funding_rate": avg_funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt strategy parameters
        # For simplicity, we just reset the cooldown.
        # self.last_signal_bar = ctx.bar_index # Cooldown is already handled in on_bar
        pass