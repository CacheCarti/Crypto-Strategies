from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetFundingDivergence(Strategy):
    METADATA = {
        "name": "Cross-Asset Funding Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt", "funding_rate_ethusdt", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # for moving average of funding
        self.divergence_threshold = 0.00005 # 0.005% for extreme divergence
        self.cooldown_bars = 10 # bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.funding_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        btc_funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0)]
        eth_funding_rates = [ctx.features.get("funding_rate_ethusdt", 0.0)]
        sol_funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0)]

        # To get historical funding, we'd need to store them. For simplicity, we'll use current for divergence
        # and assume the platform provides historical funding for SMA if needed.
        # For this strategy, we'll use the current funding for divergence and a price-based condition
        # for entry, and funding divergence as a filter.

        current_btc_funding = btc_funding_rates[-1]
        current_eth_funding = eth_funding_rates[-1]
        current_sol_funding = sol_funding_rates[-1]

        # Calculate average altcoin funding
        avg_alt_funding = (current_eth_funding + current_sol_funding) / 2.0

        # Calculate divergence
        btc_alt_funding_divergence = current_btc_funding - avg_alt_funding

        # Simple moving average of closing prices for trend
        sma_short_period = 10
        sma_long_period = 30
        sma_short = self._sma(closes, sma_short_period)
        sma_long = self._sma(closes, sma_long_period)

        if sma_short is None or sma_long is None:
            return None

        current_price = ctx.bar.close

        # Determine if BTC funding is extremely high relative to alts (crowded long)
        is_btc_funding_extreme_high = btc_alt_funding_divergence > self.divergence_threshold

        # Determine if BTC funding is extremely low relative to alts (crowded short)
        is_btc_funding_extreme_low = btc_alt_funding_divergence < -self.divergence_threshold

        # Entry logic
        if not ctx.has_position():
            # Long signal: BTC funding is extremely low (crowded shorts), and price is trending up (SMA crossover)
            if is_btc_funding_extreme_low and sma_short > sma_long and current_price > sma_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "btc_funding_low_alt_divergence_sma_cross_bullish",
                        "btc_funding": current_btc_funding,
                        "avg_alt_funding": avg_alt_funding,
                        "funding_divergence": btc_alt_funding_divergence,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "current_price": current_price,
                    }
                )

            # Short signal: BTC funding is extremely high (crowded longs), and price is trending down (SMA crossover)
            if is_btc_funding_extreme_high and sma_short < sma_long and current_price < sma_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "btc_funding_high_alt_divergence_sma_cross_bearish",
                        "btc_funding": current_btc_funding,
                        "avg_alt_funding": avg_alt_funding,
                        "funding_divergence": btc_alt_funding_divergence,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "current_price": current_price,
                    }
                )

        # Exit logic (or hold)
        elif ctx.has_position():
            # If in a long position and funding divergence reverses to neutral or extreme high, consider exiting
            if ctx.position_direction() == "long" and btc_alt_funding_divergence > -self.divergence_threshold * 0.5: # Exit if less extreme short crowding
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_exit_funding_divergence_normalize",
                        "btc_funding": current_btc_funding,
                        "avg_alt_funding": avg_alt_funding,
                        "funding_divergence": btc_alt_funding_divergence,
                    }
                )
            # If in a short position and funding divergence reverses to neutral or extreme low, consider exiting
            if ctx.position_direction() == "short" and btc_alt_funding_divergence < self.divergence_threshold * 0.5: # Exit if less extreme long crowding
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_exit_funding_divergence_normalize",
                        "btc_funding": current_btc_funding,
                        "avg_alt_funding": avg_alt_funding,
                        "funding_divergence": btc_alt_funding_divergence,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass # No adaptive logic needed for this simple example.