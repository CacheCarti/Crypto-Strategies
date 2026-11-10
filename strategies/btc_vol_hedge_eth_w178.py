from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolShockEthShort(Strategy):
    METADATA = {
        "name": "BTC Vol Shock ETH Short",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # ~2 hours
        "warmup_bars": 60, # For 30-period std dev
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 30
        self.vol_threshold_std_dev = 1.5 # How many std devs above avg vol to trigger
        self.vol_avg_period = 100 # Period to calculate historical average vol
        self.min_btc_return_pct = -0.5 # BTC must be down at least 0.5%
        self.min_eth_return_pct = -0.3 # ETH must be down at least 0.3%
        self.cooldown_bars = 6
        self.last_trade_bar_index = -self.cooldown_bars * 2 # Initialize to allow first trade

        # State to track volatility regime
        self.in_vol_shock_regime = False

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_avg_period + self.vol_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Calculate BTC volatility (using price changes as a proxy for realized vol)
        btc_returns_1h = []
        for i in range(1, len(closes)): # Using ETH closes to derive 1h returns for vol calc
            # For cross-asset volatility, we need BTC OHLCV to calculate its vol.
            # Since we only have current asset's OHLCV, we use its returns as proxy
            # or rely on external features. The prompt implies using BTC_return_pct
            # as a primary signal, which is a 24h return. For 1h vol, we'd need
            # BTC 1h closes, which we don't have.
            # Instead, we will use the *magnitude* of recent BTC_return_pct as a proxy for volatility.
            pass # We will use the feature directly.

        # Get recent BTC 24h returns
        btc_returns_24h = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.vol_period)]
        # This is not ideal as btc_return_pct is 24h, not 1h.
        # To calculate 1h realized vol, we would need 1h BTC price data.
        # Given limitations, we'll proxy "BTC realized volatility expansion"
        # by checking if the *absolute magnitude* of BTC_return_pct has recently increased significantly.

        # Let's use the standard deviation of recent 1-hour returns of the current asset (ETH)
        # as a proxy for market-wide volatility expansion, and then filter with BTC_return_pct.
        # This is a pragmatic adaptation given the available data.
        eth_1h_returns = []
        for i in range(1, len(closes)):
            eth_1h_returns.append((closes[i] - closes[i-1]) / closes[i-1] * 100) # Percentage return

        if len(eth_1h_returns) < self.vol_avg_period + self.vol_period - 1:
            return None

        current_vol = self._stddev(eth_1h_returns, self.vol_period)
        if current_vol is None:
            return None

        historical_vols = []
        for i in range(self.vol_period, len(eth_1h_returns) - self.vol_period + 1):
            hist_vol = self._stddev(eth_1h_returns[i-self.vol_period:i], self.vol_period)
            if hist_vol is not None:
                historical_vols.append(hist_vol)

        if len(historical_vols) < self.vol_avg_period:
            return None

        avg_historical_vol = sum(historical_vols[-self.vol_avg_period:]) / self.vol_avg_period
        std_dev_of_vols = self._stddev(historical_vols, self.vol_avg_period)

        if std_dev_of_vols is None or std_dev_of_vols == 0:
            # Cannot determine abnormal volatility
            self.in_vol_shock_regime = False
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Condition 1: BTC realized volatility expands abnormally
        is_vol_expanding = (current_vol > avg_historical_vol + self.vol_threshold_std_dev * std_dev_of_vols)

        # Condition 2: btc_return_pct is negative
        is_btc_weak = (btc_return_pct < self.min_btc_return_pct)

        # Condition 3: eth_return_pct also confirms weakness
        is_eth_weak = (eth_return_pct < self.min_eth_return_pct)

        # Regime entry:
        if is_vol_expanding and is_btc_weak and is_eth_weak:
            if not self.in_vol_shock_regime:
                self.in_vol_shock_regime = True
                # Only signal short if we just entered the regime
                if not ctx.has_position():
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "btc_vol_shock_eth_weakness",
                            "current_eth_vol": round(current_vol, 4),
                            "avg_hist_eth_vol": round(avg_historical_vol, 4),
                            "std_dev_of_vols": round(std_dev_of_vols, 4),
                            "btc_return_pct": round(btc_return_pct, 2),
                            "eth_return_pct": round(eth_return_pct, 2),
                        }
                    )
                elif ctx.position_direction() == "long":
                    # Flip if we are long and regime shifts to short
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        metadata={
                            "reason": "btc_vol_shock_eth_weakness_flip",
                            "current_eth_vol": round(current_vol, 4),
                            "avg_hist_eth_vol": round(avg_historical_vol, 4),
                            "std_dev_of_vols": round(std_dev_of_vols, 4),
                            "btc_return_pct": round(btc_return_pct, 2),
                            "eth_return_pct": round(eth_return_pct, 2),
                        }
                    )

        # Regime exit: When BTC volatility normalizes (or conditions no longer met)
        # We exit the regime if vol is no longer expanding abnormally OR BTC is no longer weak.
        # ETH weakness is a confirmation for entry, but the regime is primarily defined by BTC vol and direction.
        if self.in_vol_shock_regime and (not is_vol_expanding or not is_btc_weak):
            self.in_vol_shock_regime = False
            if ctx.has_position() and ctx.position_direction() == "short":
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "vol_shock_regime_ended",
                        "current_eth_vol": round(current_vol, 4),
                        "avg_hist_eth_vol": round(avg_historical_vol, 4),
                        "std_dev_of_vols": round(std_dev_of_vols, 4),
                        "is_vol_expanding": is_vol_expanding,
                        "is_btc_weak": is_btc_weak,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset regime state on close if needed, or log.
        # For this strategy, regime state is managed in on_bar based on conditions.
        pass