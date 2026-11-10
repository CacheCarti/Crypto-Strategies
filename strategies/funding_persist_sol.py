from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPersistenceFader(Strategy):
    METADATA = {
        "name": "Funding Persistence Fader SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 30, # Need funding history, EMA
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 4 # How many bars to check for funding persistence (4 hours)
        self.funding_threshold = 0.00015 # 0.015% funding rate per 8h, roughly 0.000075 per hour * 2 (for perp rate) = 0.00015
        self.ema_period = 20
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        funding_rates_history = [ctx.features.get(f"funding_rate_solusdt", 0.0) for _ in range(self.funding_period)]

        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Check for persistent high funding for shorts (long signal)
        if all(fr > self.funding_threshold for fr in funding_rates_history):
            # Price confirmation: current price below EMA suggests a potential bounce
            if ctx.bar.close < ema:
                signal_direction = "long"
                confidence = 0.75 # High confidence due to persistence and price confirmation
                reason = "persistent_high_funding_rate_below_ema"
                metadata = {
                    "funding_rate": current_funding_rate,
                    "funding_threshold": self.funding_threshold,
                    "ema_period": self.ema_period,
                    "ema_value": ema,
                    "close_price": ctx.bar.close,
                    "funding_rates_history": funding_rates_history,
                }

        # Check for persistent low/negative funding for longs (short signal)
        elif all(fr < -self.funding_threshold for fr in funding_rates_history):
            # Price confirmation: current price above EMA suggests a potential pullback
            if ctx.bar.close > ema:
                signal_direction = "short"
                confidence = 0.75 # High confidence due to persistence and price confirmation
                reason = "persistent_low_funding_rate_above_ema"
                metadata = {
                    "funding_rate": current_funding_rate,
                    "funding_threshold": -self.funding_threshold,
                    "ema_period": self.ema_period,
                    "ema_value": ema,
                    "close_price": ctx.bar.close,
                    "funding_rates_history": funding_rates_history,
                }

        if ctx.has_position():
            # If we have a position, don't open a new one
            # If we need to flip, the engine handles it by simply returning the opposite signal
            if ctx.position_direction() == "long" and signal_direction == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata=metadata)
            elif ctx.position_direction() == "short" and signal_direction == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata=metadata)
            return None # Hold current position
        elif signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this strategy yet, just reset cooldown
        pass