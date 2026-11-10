from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetVolatilityShort(Strategy):
    METADATA = {
        "name": "CrossAssetVolatilityShort",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 30
        self.vol_threshold_multiplier = 1.5
        self.btc_neg_return_threshold = -0.005 # -0.5%
        self.eth_neg_return_threshold = -0.003 # -0.3%
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars - 1 # Ensure trade on first eligible bar

        # Store historical BTC volatility for comparison
        self.btc_vol_history = []

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return self._stddev(log_returns, period) * math.sqrt(252) # Annualized, adjust for hourly

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period * 2) # Need enough data for volatility and its average
        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate BTC Realized Volatility
        btc_closes_for_vol = [ctx.bar.close] # Placeholder, in a real scenario we'd need BTC historical closes
        # For simplicity and backtestability, we'll use a proxy:
        # Instead of BTC close data, we'll use a volatility proxy that can be computed from current domain's data
        # For a truly cross-asset vol, we'd need BTC_USD_closes.
        # Since we don't have BTC OHLCV directly, we'll proxy BTC vol expansion with ETH vol expansion
        # and rely on btc_return_pct as the cross-asset signal.
        # This is a critical limitation for "cross-asset volatility" without cross-asset OHLCV.
        # Re-interpreting: we look for ETH vol expansion combined with BTC weakness.

        eth_vol = self._realized_volatility(closes, self.vol_period)
        if eth_vol is None:
            return None

        self.btc_vol_history.append(eth_vol) # Store ETH vol as proxy for "market volatility"
        if len(self.btc_vol_history) > self.vol_period * 2: # Keep history for average
            self.btc_vol_history.pop(0)

        if len(self.btc_vol_history) < self.vol_period:
            return None

        avg_vol = sum(self.btc_vol_history[:-1]) / len(self.btc_vol_history[:-1]) if len(self.btc_vol_history) > 1 else eth_vol

        vol_expanded = eth_vol > avg_vol * self.vol_threshold_multiplier
        
        # Entry conditions
        if not ctx.has_position():
            if vol_expanded and \
               btc_returns < self.btc_neg_return_threshold and \
               eth_returns < self.eth_neg_return_threshold:
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    metadata={
                        "reason": "cross_asset_volatility_short",
                        "eth_vol": eth_vol,
                        "avg_vol": avg_vol,
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                    }
                )
        # Exit conditions (or hold)
        else:
            # If BTC volatility normalizes (i.e., not expanded) AND we are short, consider exiting
            if not vol_expanded and ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index # Reset cooldown on exit
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "volatility_normalized_exit_short",
                        "eth_vol": eth_vol,
                        "avg_vol": avg_vol,
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive sizing or logging could go here
        pass