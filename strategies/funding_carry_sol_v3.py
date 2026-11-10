from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingCarrySOL(Strategy):
    METADATA = {
        "name": "Funding Carry SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for longer holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24-hour funding average
        self.ema_period = 20 # For price confirmation
        self.min_funding_threshold = 0.00015 # 0.015% per 8h, or 0.045% daily for long
        self.max_funding_threshold = -0.00015 # For short
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2) # Need enough data for initial EMA
        highs = ctx.highs(self.ema_period * 2)
        lows = ctx.lows(self.ema_period * 2)
        volumes = ctx.volumes(self.ema_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Get funding rate, default to 0.0 if not available
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Simple average of recent funding rates for a smoother signal
        # We need funding rates for the last `funding_period` bars
        # This requires storing past funding rates or fetching from a hypothetical feature stream
        # For simplicity, we will use the current funding rate as the primary signal,
        # but acknowledge a smoothed version would be better if historical feature data were directly accessible.
        # As per contract, only current bar features are directly accessible unless explicitly stored.
        # For this example, we'll use the current funding rate, implicitly assuming it's an 8h rate.
        # To smooth, we'd need to store `funding_rate` history in `self` and calculate EMA/SMA on it.
        # Given the output budget, we'll keep it simple and use the latest funding rate for now.

        # Long Entry Conditions:
        # 1. Funding rate is positive and above threshold (longs pay shorts)
        # 2. Price is above its EMA (bullish momentum)
        if not ctx.has_position():
            if funding_rate > self.min_funding_threshold and current_price > ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_carry_long_entry",
                        "funding_rate": funding_rate,
                        "current_price": current_price,
                        "ema": ema,
                        "funding_threshold": self.min_funding_threshold
                    }
                )

            # Short Entry Conditions:
            # 1. Funding rate is negative and below threshold (shorts pay longs)
            # 2. Price is below its EMA (bearish momentum)
            elif funding_rate < self.max_funding_threshold and current_price < ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_carry_short_entry",
                        "funding_rate": funding_rate,
                        "current_price": current_price,
                        "ema": ema,
                        "funding_threshold": self.max_funding_threshold
                    }
                )

        # Exit Conditions (for existing positions):
        # Exit if funding condition normalizes (i.e., funding rate crosses back towards zero)
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding rate drops below a neutral zone, or turns negative
                if funding_rate < self.min_funding_threshold / 2: # Exit if funding carry significantly reduces
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "funding_carry_long_exit_normalized",
                            "funding_rate": funding_rate,
                            "current_price": current_price,
                            "ema": ema,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if funding rate rises above a neutral zone, or turns positive
                if funding_rate > self.max_funding_threshold / 2: # Exit if funding carry significantly reduces
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "funding_carry_short_exit_normalized",
                            "funding_rate": funding_rate,
                            "current_price": current_price,
                            "ema": ema,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log position close details for analysis
        pass