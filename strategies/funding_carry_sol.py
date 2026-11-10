from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional
import math

class SolFundingCarry(Strategy):
    METADATA = {
        "name": "SOL Funding Carry",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_threshold = -0.00005  # Slightly negative funding for long bias
        self.funding_short_threshold = 0.00005 # Slightly positive funding for short bias
        self.funding_neutral_threshold = 0.00002 # Funding rate to exit position
        self.ema_period = 20
        self.cooldown_bars = 6
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.ema_period + 1:
            return None

        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        current_close = ctx.bar.close
        ema = self._ema(closes, self.ema_period)

        if ema is None:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding becomes less favorable (less negative or positive)
                if current_funding_rate > -self.funding_neutral_threshold:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_funding_normalized",
                        "funding_rate": current_funding_rate,
                        "exit_threshold": -self.funding_neutral_threshold
                    })
            elif ctx.position_direction() == "short":
                # Exit short if funding becomes less favorable (less positive or negative)
                if current_funding_rate < self.funding_neutral_threshold:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_funding_normalized",
                        "funding_rate": current_funding_rate,
                        "exit_threshold": self.funding_neutral_threshold
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        # Long entry: funding is sufficiently negative AND price is above EMA
        if current_funding_rate < self.funding_long_threshold and current_close > ema:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, metadata={
                "reason": "long_entry_funding_negative_ema_bullish",
                "funding_rate": current_funding_rate,
                "funding_threshold": self.funding_long_threshold,
                "price": current_close,
                "ema": ema
            })

        # Short entry: funding is sufficiently positive AND price is below EMA
        if current_funding_rate > self.funding_short_threshold and current_close < ema:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, metadata={
                "reason": "short_entry_funding_positive_ema_bearish",
                "funding_rate": current_funding_rate,
                "funding_threshold": self.funding_short_threshold,
                "price": current_close,
                "ema": ema
            })

        return None

    def on_position_close(self, ctx: BarContext, position: dict) -> None:
        # No adaptive logic for now, just reset cooldown if needed (though on_bar handles it)
        pass