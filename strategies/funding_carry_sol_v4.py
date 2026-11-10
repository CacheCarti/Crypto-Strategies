from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingCarrySol(Strategy):
    METADATA = {
        "name": "FundingCarrySol",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing for carry to play out
        "warmup_bars": 50, # Needs enough bars for EMA and funding rate history
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_long_threshold = 0.0001  # Positive funding for long (0.01% per 8h)
        self.funding_short_threshold = -0.0001 # Negative funding for short (-0.01% per 8h)
        self.funding_exit_threshold = 0.00005 # Exit if funding normalizes
        self.min_ema_diff_bps = 5 # Minimum EMA difference for confirmation
        self.cooldown_bars = 10 # Cooldown after a trade
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
        closes = ctx.closes(self.slow_ema_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Avoid trading in extreme volatility or crisis
        if regime in ["CRISIS", "MELTDOWN"]:
            if ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit", "regime": regime})
            return None

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding normalizes or turns negative
                if funding_rate < self.funding_exit_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_long_exit", "funding_rate": funding_rate})
                # Exit long if price crosses below slow EMA, indicating trend shift
                if current_price < slow_ema and fast_ema < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "bearish_ema_cross_long_exit", "fast_ema": fast_ema, "slow_ema": slow_ema, "price": current_price})

            elif ctx.position_direction() == "short":
                # Exit short if funding normalizes or turns positive
                if funding_rate > -self.funding_exit_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_short_exit", "funding_rate": funding_rate})
                # Exit short if price crosses above slow EMA, indicating trend shift
                if current_price > slow_ema and fast_ema > slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "bullish_ema_cross_short_exit", "fast_ema": fast_ema, "slow_ema": slow_ema, "price": current_price})
            
            return None # Hold position if no exit condition met

        # Entry logic
        if fast_ema is None or slow_ema is None:
            return None

        ema_diff_bps = ((fast_ema - slow_ema) / current_price) * 10000

        # Long entry conditions
        if (
            funding_rate > self.funding_long_threshold and # Favorable funding for long
            fast_ema > slow_ema and # Bullish EMA crossover/alignment
            ema_diff_bps > self.min_ema_diff_bps and # Sufficient EMA separation
            current_price > slow_ema and # Price above slow EMA
            trend_regime != "bear" # Avoid entering long in a bear trend
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.65,
                metadata={
                    "reason": "funding_carry_long_entry",
                    "funding_rate": funding_rate,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "ema_diff_bps": ema_diff_bps,
                    "price": current_price,
                    "trend_regime": trend_regime
                }
            )

        # Short entry conditions
        elif (
            funding_rate < self.funding_short_threshold and # Favorable funding for short
            fast_ema < slow_ema and # Bearish EMA crossover/alignment
            ema_diff_bps < -self.min_ema_diff_bps and # Sufficient EMA separation (negative)
            current_price < slow_ema and # Price below slow EMA
            trend_regime != "bull" # Avoid entering short in a bull trend
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.65,
                metadata={
                    "reason": "funding_carry_short_entry",
                    "funding_rate": funding_rate,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "ema_diff_bps": ema_diff_bps,
                    "price": current_price,
                    "trend_regime": trend_regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Placeholder for adaptive logic, not implemented in this version
        pass