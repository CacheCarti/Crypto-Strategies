from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,  # Wider SL for swing
        "declared_tp_bps": 500.0,  # Wider TP for swing
        "declared_hold_seconds": 7200, # 2-hour average hold
        "warmup_bars": 50, # Need enough bars for EMA and funding rate history
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 12
        self.slow_ema_period = 26
        self.funding_rate_threshold_long = -0.0002 # Very negative funding for long
        self.funding_rate_threshold_short = 0.0002 # Very positive funding for short
        self.cooldown_bars = 10 # Cooldown after a trade
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
        closes = ctx.closes(self.slow_ema_period + 1) # +1 for EMA calculation
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Causal Structure: Funding rate extreme combined with price momentum confirmation
        # Entry logic
        if not ctx.has_position():
            # Long setup: Very negative funding rate (shorts paying longs) AND price is showing upward momentum (fast EMA > slow EMA)
            if funding_rate < self.funding_rate_threshold_long and fast_ema > slow_ema and closes[-1] > closes[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_rate_reversion_long",
                        "funding_rate": funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "price": current_price,
                    }
                )

            # Short setup: Very positive funding rate (longs paying shorts) AND price is showing downward momentum (fast EMA < slow EMA)
            elif funding_rate > self.funding_rate_threshold_short and fast_ema < slow_ema and closes[-1] < closes[-2]:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_rate_reversion_short",
                        "funding_rate": funding_rate,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "price": current_price,
                    }
                )

        # Exit logic
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding rate normalizes or momentum reverses
                if funding_rate >= -0.00005 or fast_ema < slow_ema: # Normalize funding to near zero or cross down
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_momentum_reversed",
                            "funding_rate": funding_rate,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if funding rate normalizes or momentum reverses
                if funding_rate <= 0.00005 or fast_ema > slow_ema: # Normalize funding to near zero or cross up
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_momentum_reversed",
                            "funding_rate": funding_rate,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this simple strategy, just logging.
        # print(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']:.2f} bps after {position['bars_held']} bars.")
        pass