from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SqueezeDivergenceFunding(Strategy):
    METADATA = {
        "name": "Squeeze Divergence Funding",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_rate_threshold = -0.0001 # Negative funding rate threshold for longs
        self.funding_rate_positive_threshold = 0.0001 # Positive funding rate threshold for shorts
        self.cooldown_bars = 5
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

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long condition: Price rising (fast EMA > slow EMA) while funding rate is negative (trapped shorts)
        if not ctx.has_position() and fast_ema > slow_ema and funding_rate < self.funding_rate_threshold:
            # Add a check for recent price increase to confirm "rising price"
            # Compare current close to an earlier close to detect recent upward momentum
            if len(closes) > self.fast_ema_period and current_price > closes[-self.fast_ema_period]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "squeeze_divergence_funding_long",
                        "current_price": current_price,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "funding_rate": funding_rate,
                        "funding_threshold": self.funding_rate_threshold,
                    }
                )

        # Short condition: Price falling (fast EMA < slow EMA) while funding rate is positive (trapped longs)
        if not ctx.has_position() and fast_ema < slow_ema and funding_rate > self.funding_rate_positive_threshold:
            # Add a check for recent price decrease to confirm "falling price"
            # Compare current close to an earlier close to detect recent downward momentum
            if len(closes) > self.fast_ema_period and current_price < closes[-self.fast_ema_period]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "squeeze_divergence_funding_short",
                        "current_price": current_price,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "funding_rate": funding_rate,
                        "funding_threshold": self.funding_rate_positive_threshold,
                    }
                )

        # Optional: Exit if funding rate normalizes or EMAs cross against position
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding turns positive or EMAs cross bearishly
                if funding_rate > 0 or fast_ema < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_ema_crossed",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "funding_rate": funding_rate,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if funding turns negative or EMAs cross bullishly
                if funding_rate < 0 or fast_ema > slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_ema_crossed",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "funding_rate": funding_rate,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently, just logging
        pass