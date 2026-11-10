from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergenceBTC(Strategy):
    METADATA = {
        "name": "Funding Divergence BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold on 1h bars
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.price_ma_period = 20
        self.funding_ma_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_ma_period)
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_ma_period)] # Get current funding rate

        if len(closes) < self.price_ma_period or len(funding_rates) < self.funding_ma_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        prev_price = ctx.closes(2)[0] if len(ctx.closes(2)) == 2 else current_price
        price_change = (current_price - prev_price) / prev_price * 100

        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        
        # Simple moving average of price for trend confirmation
        price_ma = self._sma(closes, self.price_ma_period)
        if price_ma is None:
            return None

        # Simple moving average of funding rate
        funding_ma = self._sma(funding_rates, self.funding_ma_period)
        if funding_ma is None:
            return None

        # Long condition: Price rising, but funding is negative or becoming less positive
        # This suggests a short squeeze or bears covering while price moves up
        if not ctx.has_position():
            if price_change > 0.1 and current_price > price_ma and current_funding_rate < 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "long_funding_divergence",
                        "price_change_pct": price_change,
                        "current_price": current_price,
                        "price_ma": price_ma,
                        "current_funding_rate": current_funding_rate,
                        "funding_ma": funding_ma,
                    },
                )

            # Short condition: Price falling, but funding is positive or becoming less negative
            # This suggests a long liquidation or bulls covering while price moves down
            elif price_change < -0.1 and current_price < price_ma and current_funding_rate > 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "short_funding_divergence",
                        "price_change_pct": price_change,
                        "current_price": current_price,
                        "price_ma": price_ma,
                        "current_funding_rate": current_funding_rate,
                        "funding_ma": funding_ma,
                    },
                )
        elif ctx.has_position():
            # Exit logic: if price crosses moving average against position or funding rate reverses significantly
            if ctx.position_direction() == "long":
                if current_price < price_ma or current_funding_rate > 0.0001: # Exit if price falls below MA or funding turns positive
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_price_ma_cross_or_funding_reversal",
                            "current_price": current_price,
                            "price_ma": price_ma,
                            "current_funding_rate": current_funding_rate,
                        },
                    )
            elif ctx.position_direction() == "short":
                if current_price > price_ma or current_funding_rate < -0.0001: # Exit if price rises above MA or funding turns negative
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_price_ma_cross_or_funding_reversal",
                            "current_price": current_price,
                            "price_ma": price_ma,
                            "current_funding_rate": current_funding_rate,
                        },
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy currently, just logging
        pass