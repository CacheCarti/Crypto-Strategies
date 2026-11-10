from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFundingDivergence(Strategy):
    METADATA = {
        "name": "SOL Funding Divergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_rate_threshold = -0.00015 # Very negative funding rate for long opportunities
        self.price_change_threshold = 0.005 # 0.5% price increase/decrease
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
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
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Price change over the last few bars for confirmation
        price_change_fast_period = 3
        if len(closes) < price_change_fast_period + 1:
            return None
        price_change_pct = (closes[-1] - closes[-price_change_fast_period]) / closes[-price_change_fast_period]

        if not ctx.has_position():
            # Long condition: Price rising with very negative funding rate (short squeeze signal)
            if (current_funding_rate < self.funding_rate_threshold and
                price_change_pct > self.price_change_threshold and
                fast_ema > slow_ema): # Price confirmation
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_divergence_long_squeeze",
                        "funding_rate": current_funding_rate,
                        "price_change_pct": price_change_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": ctx.bar.close
                    }
                )

            # Short condition: Price falling with very positive funding rate (long liquidation signal)
            elif (current_funding_rate > -self.funding_rate_threshold and # Symmetrical threshold for positive funding
                  price_change_pct < -self.price_change_threshold and
                  fast_ema < slow_ema): # Price confirmation
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_divergence_short_liquidation",
                        "funding_rate": current_funding_rate,
                        "price_change_pct": price_change_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": ctx.bar.close
                    }
                )
        elif ctx.position_direction() == "long":
            # Exit long if fast EMA crosses below slow EMA or funding rate normalizes
            if fast_ema < slow_ema or current_funding_rate > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_long_ema_cross_or_funding_normalized",
                        "funding_rate": current_funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": ctx.bar.close
                    }
                )
        elif ctx.position_direction() == "short":
            # Exit short if fast EMA crosses above slow EMA or funding rate normalizes
            if fast_ema > slow_ema or current_funding_rate < 0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_short_ema_cross_or_funding_normalized",
                        "funding_rate": current_funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": ctx.bar.close
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy, just logging.
        pass