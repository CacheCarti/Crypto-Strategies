from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ExtremeFundingReversion(Strategy):
    METADATA = {
        "name": "Extreme Funding Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Look back 24 hours for extreme funding
        self.funding_threshold = 0.0002 # 0.02% per 8h, multiplied by 3 for 24h funding -> 0.0006 for 24h
        self.persistence_bars = 4 # How many consecutive bars funding must be extreme
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Short-term EMA for price confirmation
        self.ema_period = 20

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

        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.ema_period + 1:
            return None

        current_close = ctx.bar.close
        ema = self._ema(closes, self.ema_period)

        if ema is None:
            return None

        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # The funding rate feature is hourly, so we sum up for a longer window to check persistence
        # Note: The feature itself is 'per 8h', so 0.0001 means 0.01% per 8 hours.
        # We're looking for extreme values over several bars.

        # Check for extreme funding persistence
        extreme_long_funding_count = 0
        extreme_short_funding_count = 0
        
        # We need at least 'persistence_bars' of funding data
        if len(funding_rates) < self.persistence_bars:
            return None

        # Check recent funding rates for persistence
        for i in range(1, self.persistence_bars + 1):
            # Funding rate is typically positive for long bias, negative for short bias
            # We are looking for extreme positive funding (too many longs) or extreme negative funding (too many shorts)
            # to fade it.
            current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
            if current_funding > self.funding_threshold:
                extreme_long_funding_count += 1
            elif current_funding < -self.funding_threshold:
                extreme_short_funding_count += 1
            else:
                # Reset counts if funding isn't extreme
                extreme_long_funding_count = 0
                extreme_short_funding_count = 0
            
            # Look at the past `persistence_bars` readings
            if i < self.persistence_bars:
                # To avoid re-checking, we can break if we've already seen enough persistence in the loop
                # This logic is a bit tricky with "current_funding" inside the loop.
                # A better approach is to get a slice of recent funding rates and then count.
                pass 
        
        # Re-evaluate funding persistence over the exact `persistence_bars` window
        recent_funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0, offset=k) for k in range(self.persistence_bars)]

        is_persistent_extreme_long_funding = all(fr > self.funding_threshold for fr in recent_funding_rates)
        is_persistent_extreme_short_funding = all(fr < -self.funding_threshold for fr in recent_funding_rates)


        signal_direction = "hold"
        signal_confidence = 0.0
        reason = "no_signal"
        metadata = {}

        if not ctx.has_position():
            # Fade extreme positive funding (too many longs -> short)
            if is_persistent_extreme_long_funding and current_close > ema: # Price confirmation: only short if above EMA (fading strength)
                signal_direction = "short"
                signal_confidence = 0.75
                reason = "extreme_long_funding_reversion_above_ema"
                metadata = {
                    "funding_rate": recent_funding_rates[-1],
                    "funding_threshold": self.funding_threshold,
                    "persistence_bars": self.persistence_bars,
                    "current_close": current_close,
                    "ema": ema,
                }
            # Fade extreme negative funding (too many shorts -> long)
            elif is_persistent_extreme_short_funding and current_close < ema: # Price confirmation: only long if below EMA (fading weakness)
                signal_direction = "long"
                signal_confidence = 0.75
                reason = "extreme_short_funding_reversion_below_ema"
                metadata = {
                    "funding_rate": recent_funding_rates[-1],
                    "funding_threshold": self.funding_threshold,
                    "persistence_bars": self.persistence_bars,
                    "current_close": current_close,
                    "ema": ema,
                }

        if signal_direction != "hold":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown.
        # self.last_trade_bar is already updated on signal.
        pass