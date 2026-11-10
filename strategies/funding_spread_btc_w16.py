from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetFundingDivergence(Strategy):
    METADATA = {
        "name": "Cross-Asset Funding Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt", "funding_rate_ethusdt", "funding_rate_solusdt", "eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24-hour window for funding comparison
        self.funding_std_dev_multiplier = 1.5 # How many std devs for extreme funding
        self.entry_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.entry_cooldown_bars # Initialize to allow immediate trade

        # For simple price trend confirmation
        self.ema_short_period = 10
        self.ema_long_period = 30

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Get funding rates and returns
        btc_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        sol_funding = ctx.features.get("funding_rate_solusdt", 0.0)

        eth_return = ctx.features.get("eth_return_pct", 0.0)
        sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Calculate average altcoin funding and relative BTC funding
        alt_fundings = [ctx.features.get("funding_rate_ethusdt", 0.0), ctx.features.get("funding_rate_solusdt", 0.0)]
        
        # Ensure we have enough data for recent funding history for std dev calculation
        # This is a proxy, as we only get current funding, not history.
        # We'll use a simplified approach: just compare current BTC funding to alt funding
        # and use altcoin returns as a proxy for altcoin market strength/weakness.

        # Calculate a "composite alt funding sentiment"
        # High alt funding + positive alt returns suggest strong alt momentum / crowded alt long
        # Low alt funding + negative alt returns suggest weak alt momentum / crowded alt short
        alt_composite_score = (eth_funding + sol_funding) / 2.0 + (eth_return + sol_return) / 200.0 # Scale returns

        # Price trend confirmation
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        current_price = ctx.bar.close

        # Strategy Logic:
        # Look for extreme BTC funding relative to altcoin funding and market sentiment.
        # This implies a potential divergence in positioning.

        signal_direction: Optional[str] = None
        reason = ""
        confidence = 0.0

        # Long signal: BTC funding is significantly negative (crowded short),
        # while altcoins are showing some strength (positive alt_composite_score).
        # And BTC price is trending up (EMA short > EMA long).
        if btc_funding < -0.0002 and alt_composite_score > 0.0001 and ema_short > ema_long and current_price > ema_short:
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add a touch of randomness
                reason = "btc_funding_extreme_negative_alt_strength_ema_cross_up"
        
        # Short signal: BTC funding is significantly positive (crowded long),
        # while altcoins are showing weakness (negative alt_composite_score).
        # And BTC price is trending down (EMA short < EMA long).
        elif btc_funding > 0.0002 and alt_composite_score < -0.0001 and ema_short < ema_long and current_price < ema_short:
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1)
                reason = "btc_funding_extreme_positive_alt_weakness_ema_cross_down"
        
        # If we have a position and conditions reverse, or if the trend breaks, consider flattening
        if ctx.has_position():
            if ctx.position_direction() == "long" and (btc_funding > -0.0001 or alt_composite_score < 0.00005 or ema_short < ema_long):
                signal_direction = "flat"
                reason = "long_exit_funding_alt_or_trend_reversal"
                confidence = 0.5
            elif ctx.position_direction() == "short" and (btc_funding < 0.0001 or alt_composite_score > -0.00005 or ema_short > ema_long):
                signal_direction = "flat"
                reason = "short_exit_funding_alt_or_trend_reversal"
                confidence = 0.5

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "btc_funding": btc_funding,
                    "eth_funding": eth_funding,
                    "sol_funding": sol_funding,
                    "eth_return_pct": eth_return,
                    "sol_return_pct": sol_return,
                    "alt_composite_score": alt_composite_score,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "current_price": current_price,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, regardless of profit/loss
        self.last_trade_bar = ctx.bar_index
        # Log closing details if needed
        # ctx.log_info(f"Position closed: {position['direction']} {position['return_bps']} bps, held {position['bars_held']} bars.")
        pass