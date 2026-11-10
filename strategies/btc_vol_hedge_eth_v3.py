from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolHedgeEth(Strategy):
    METADATA = {
        "name": "BTC Volatility Hedge ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,  # Need enough bars for 20-period std dev and 24h returns
        "required_features": ["btc_return_pct", "eth_return_pct"], # For cross-asset returns
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20 # Period for realized volatility (std dev of returns)
        self.vol_threshold_multiplier = 1.5 # How many std devs above avg for "expanded" vol
        self.vol_avg_period = 60 # Period to calculate average historical volatility
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State variables to track vol regime and transitions
        self.btc_vol_expanded = False
        self.btc_vol_normalizing = False

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.vol_avg_period + 1)
        if len(closes) < self.vol_avg_period + 1:
            return None

        # Calculate BTC returns for volatility
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) / 100.0 for _ in range(self.vol_avg_period)]
        if len(btc_returns) < self.vol_avg_period: # Ensure enough data for vol calculation
            return None

        current_btc_vol = self._stddev(btc_returns[-self.vol_period:], self.vol_period)
        if current_btc_vol is None:
            return None

        # Calculate historical average BTC volatility
        historical_btc_vols = []
        for i in range(self.vol_period, len(btc_returns) + 1):
            vol = self._stddev(btc_returns[i-self.vol_period:i], self.vol_period)
            if vol is not None:
                historical_btc_vols.append(vol)

        if len(historical_btc_vols) < self.vol_avg_period:
            return None

        avg_historical_btc_vol = sum(historical_btc_vols[-self.vol_avg_period:]) / self.vol_avg_period

        # Determine volatility regime
        prev_btc_vol_expanded = self.btc_vol_expanded
        self.btc_vol_expanded = current_btc_vol > avg_historical_btc_vol * self.vol_threshold_multiplier
        self.btc_vol_normalizing = current_btc_vol < avg_historical_btc_vol * (self.vol_threshold_multiplier - 0.2) # Normalizing threshold

        # Get current returns
        current_btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        current_eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Strategy logic: Short ETH when BTC vol expands AND BTC/ETH returns are negative
        if not ctx.has_position():
            # Entry condition: BTC vol just expanded, and both BTC/ETH are weak
            if self.btc_vol_expanded and not prev_btc_vol_expanded: # Volatility just expanded
                if current_btc_return_pct < -0.5 and current_eth_return_pct < -0.5: # Both BTC and ETH showing weakness
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "btc_vol_expansion_eth_weakness",
                            "current_btc_vol": current_btc_vol,
                            "avg_historical_btc_vol": avg_historical_btc_vol,
                            "btc_vol_threshold": avg_historical_btc_vol * self.vol_threshold_multiplier,
                            "btc_return_pct": current_btc_return_pct,
                            "eth_return_pct": current_eth_return_pct,
                        }
                    )
        elif ctx.position_direction() == "short":
            # Exit condition: BTC volatility normalizes OR market shows signs of recovery
            if self.btc_vol_normalizing: # Volatility is normalizing
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "btc_vol_normalizing",
                        "current_btc_vol": current_btc_vol,
                        "avg_historical_btc_vol": avg_historical_btc_vol,
                        "btc_vol_normalizing_threshold": avg_historical_btc_vol * (self.vol_threshold_multiplier - 0.2),
                    }
                )
            # Or if ETH shows strong recovery against BTC weakness (not ideal for this strategy)
            if current_eth_return_pct > 0.5 and current_btc_return_pct < -0.5: # ETH recovers while BTC still weak
                 self.last_trade_bar = ctx.bar_index
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "eth_recovery_against_btc_weakness",
                        "btc_return_pct": current_btc_return_pct,
                        "eth_return_pct": current_eth_return_pct,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing for immediate re-entry if conditions are met
        # (though on_bar's cooldown logic will still apply for self.cooldown_bars)
        pass