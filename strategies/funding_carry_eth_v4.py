from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingCarryEth(Strategy):
    METADATA = {
        "name": "FundingCarryEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24  # Lookback for funding rate average
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Get funding rate
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Market regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Entry Conditions:
        # 1. Negative funding rate (favorable carry for longs)
        # 2. Price confirmation: EMA fast above EMA slow (bullish trend)
        # 3. Market not in crisis
        # 4. Not already in a position
        if not ctx.has_position():
            if funding_rate < -0.00005 and \
               ema_fast > ema_slow and \
               regime != "CRISIS" and \
               trend_regime != "bear":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_carry_long_ema_cross",
                        "funding_rate": funding_rate,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )

        # Exit Conditions:
        # 1. If in a long position and funding rate turns positive (carry no longer favorable)
        # 2. Or if EMA fast crosses below EMA slow (trend reversal)
        if ctx.has_position() and ctx.position_direction() == "long":
            if funding_rate >= 0.00002 or ema_fast < ema_slow:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_carry_long_exit",
                        "exit_trigger_funding_rate": funding_rate,
                        "exit_trigger_ema_fast": ema_fast,
                        "exit_trigger_ema_slow": ema_slow,
                        "price": current_price,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details
        pass