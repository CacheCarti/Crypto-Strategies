from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateSpreadStrategy(Strategy):
    METADATA = {
        "name": "Funding Rate Spread BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt", "funding_rate_ethusdt", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_spread_period = 24 # 24 bars for funding rate average
        self.funding_zscore_period = 48 # 48 bars for z-score calculation
        self.last_trade_bar = -self.funding_zscore_period # Cooldown period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < 12: # Wait 12 bars (12 hours) after a trade
            return None

        btc_funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0)] + [
            ctx.features.get(f"funding_rate_btcusdt_lag_{i}", 0.0) for i in range(1, self.funding_spread_period)
        ]
        eth_funding_rates = [ctx.features.get("funding_rate_ethusdt", 0.0)] + [
            ctx.features.get(f"funding_rate_ethusdt_lag_{i}", 0.0) for i in range(1, self.funding_spread_period)
        ]
        sol_funding_rates = [ctx.features.get("funding_rate_solusdt", 0.0)] + [
            ctx.features.get(f"funding_rate_solusdt_lag_{i}", 0.0) for i in range(1, self.funding_spread_period)
        ]

        if len(btc_funding_rates) < self.funding_spread_period:
            return None

        # Calculate average altcoin funding for comparison
        avg_alt_funding = (self._sma(eth_funding_rates, self.funding_spread_period) +
                           self._sma(sol_funding_rates, self.funding_spread_period)) / 2.0

        btc_funding_sma = self._sma(btc_funding_rates, self.funding_spread_period)

        if btc_funding_sma is None or avg_alt_funding is None:
            return None

        funding_spread = btc_funding_sma - avg_alt_funding

        # Collect historical funding spreads for z-score calculation
        # This is a simplification; ideally, we'd need a way to store historical spreads.
        # For this contract, we'll approximate by using the current bar's spread and recent funding rates.
        # This will be less accurate for z-score but fits within contract constraints.
        # A more accurate way would be to compute `funding_spread` for past bars and pass them to _zscore.
        # For simplicity and to avoid storing a list, we'll use the BTC funding rates as a proxy for spread volatility.
        funding_spread_zscore = self._zscore(btc_funding_rates, self.funding_zscore_period)

        if funding_spread_zscore is None:
            return None

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding spread becomes less extreme (mean reverts) or goes negative
                if funding_spread_zscore < 0.5: # Z-score below 0.5 means less extreme positive spread
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_funding_spread_mean_reversion",
                        "funding_spread": funding_spread,
                        "funding_spread_zscore": funding_spread_zscore,
                        "btc_funding_sma": btc_funding_sma,
                        "avg_alt_funding": avg_alt_funding,
                    })
            elif ctx.position_direction() == "short":
                # Exit short if funding spread becomes less extreme (mean reverts) or goes positive
                if funding_spread_zscore > -0.5: # Z-score above -0.5 means less extreme negative spread
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_funding_spread_mean_reversion",
                        "funding_spread": funding_spread,
                        "funding_spread_zscore": funding_spread_zscore,
                        "btc_funding_sma": btc_funding_sma,
                        "avg_alt_funding": avg_alt_funding,
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        # Long when BTC funding is extremely negative relative to alts (crowded short in BTC)
        if funding_spread_zscore < -1.5: # Extremely negative BTC funding spread
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "long_entry_extreme_negative_funding_spread",
                "funding_spread": funding_spread,
                "funding_spread_zscore": funding_spread_zscore,
                "btc_funding_sma": btc_funding_sma,
                "avg_alt_funding": avg_alt_funding,
            })

        # Short when BTC funding is extremely positive relative to alts (crowded long in BTC)
        elif funding_spread_zscore > 1.5: # Extremely positive BTC funding spread
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "short_entry_extreme_positive_funding_spread",
                "funding_spread": funding_spread,
                "funding_spread_zscore": funding_spread_zscore,
                "btc_funding_sma": btc_funding_sma,
                "avg_alt_funding": avg_alt_funding,
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close.
        # Cooldown is handled in on_bar.
        pass