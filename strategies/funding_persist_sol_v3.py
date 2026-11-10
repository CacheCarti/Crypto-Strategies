from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingPersistence(Strategy):
    METADATA = {
        "name": "SOL Funding Persistence Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6,  # Targeting ~6 hours hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 4  # Number of consecutive bars to check funding persistence
        self.funding_threshold = 0.00015  # 0.015% funding rate for extreme
        self.price_confirm_period = 20 # Period for price confirmation EMA
        self.cooldown_bars = 10 # Bars to wait after a trade
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
        closes = ctx.closes(self.price_confirm_period + 1)
        funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0) for _ in range(self.funding_period)] # Get current and past funding rates
        
        if len(closes) < self.METADATA["warmup_bars"] or len(funding_rates) < self.funding_period:
            return None

        if ctx.has_position():
            return None # Already in a position, wait for it to close

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        current_price = ctx.bar.close
        ema_short = self._ema(closes, self.price_confirm_period)
        if ema_short is None:
            return None

        # Check for persistent extreme positive funding (short signal)
        if all(f > self.funding_threshold for f in funding_rates[-self.funding_period:]):
            # Price confirmation: current price is above EMA (indicating strength despite high funding)
            if current_price > ema_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_persistence_fade_short",
                        "funding_rates": funding_rates[-self.funding_period:],
                        "funding_threshold": self.funding_threshold,
                        "current_price": current_price,
                        "ema_short": ema_short
                    }
                )

        # Check for persistent extreme negative funding (long signal)
        if all(f < -self.funding_threshold for f in funding_rates[-self.funding_period:]):
            # Price confirmation: current price is below EMA (indicating weakness despite low funding)
            if current_price < ema_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_persistence_fade_long",
                        "funding_rates": funding_rates[-self.funding_period:],
                        "funding_threshold": -self.funding_threshold,
                        "current_price": current_price,
                        "ema_short": ema_short
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters
        pass