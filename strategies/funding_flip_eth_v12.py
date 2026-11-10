from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipRegimeGated(Strategy):
    METADATA = {
        "name": "Funding Flip Regime Gated ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 3  # Look back this many bars for funding flip
        self.price_confirm_period = 3 # Look back this many bars for price confirmation
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For price confirmation (EMA crossover)
        self.fast_ema_period = 8
        self.slow_ema_period = 21

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA value
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        previous_fundings = [ctx.features.get("funding_rate_ethusdt", 0.0, offset=i) for i in range(1, self.funding_period + 1)]

        # Calculate EMAs for price confirmation
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # LONG Logic
        # Funding flips from negative to positive
        funding_flip_long = (current_funding > 0 and all(p < 0 for p in previous_fundings))

        # Price confirmation: fast EMA crosses above slow EMA within the last few bars
        price_confirm_long = False
        if fast_ema > slow_ema:
            # Check if it was recently below
            for i in range(1, self.price_confirm_period + 1):
                prev_fast_ema = self._ema(ctx.closes(self.warmup_bars - i), self.fast_ema_period)
                prev_slow_ema = self._ema(ctx.closes(self.warmup_bars - i), self.slow_ema_period)
                if prev_fast_ema is not None and prev_slow_slow is not None and prev_fast_ema < prev_slow_ema:
                    price_confirm_long = True
                    break

        # SHORt Logic
        # Funding flips from positive to negative
        funding_flip_short = (current_funding < 0 and all(p > 0 for p in previous_fundings))

        # Price confirmation: fast EMA crosses below slow EMA within the last few bars
        price_confirm_short = False
        if fast_ema < slow_ema:
            # Check if it was recently above
            for i in range(1, self.price_confirm_period + 1):
                prev_fast_ema = self._ema(ctx.closes(self.warmup_bars - i), self.fast_ema_period)
                prev_slow_ema = self._ema(ctx.closes(self.warmup_bars - i), self.slow_ema_period)
                if prev_fast_ema is not None and prev_slow_ema is not None and prev_fast_ema > prev_slow_ema:
                    price_confirm_short = True
                    break

        if ctx.has_position():
            # If we have a position, don't open another one
            return None

        # Entry Conditions
        if funding_flip_long and price_confirm_long:
            # Only go long if not in a bearish trend or crisis regime
            if trend_regime != "bear" and regime != "CRISIS":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_long_ema_confirm",
                        "current_funding_rate": current_funding,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )
        elif funding_flip_short and price_confirm_short:
            # Only go short if not in a bullish trend or crisis regime
            if trend_regime != "bull" and regime != "CRISIS":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_short_ema_confirm",
                        "current_funding_rate": current_funding,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "regime": regime,
                        "trend_regime": trend_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset the last trade bar
        self.last_trade_bar = ctx.bar_index
        pass