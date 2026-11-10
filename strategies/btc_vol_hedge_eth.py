from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class BtcVolHedgeEth(Strategy):
    METADATA = {
        "name": "BTC Vol Hedge ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Target 4-hour hold
        "warmup_bars": 60, # Need enough bars for 20-period std dev
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.vol_threshold_multiplier = 1.5 # Volatility above this multiplier of its recent average
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1) # Need one more for returns
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate BTC realized volatility (using close-to-close returns)
        btc_closes = ctx.features.get("btc_closes", []) # Assuming BTC closes are available for vol calc
        if len(btc_closes) < self.vol_period + 1:
             return None # Not enough data for BTC vol

        btc_returns = [ (btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1] for i in range(1, len(btc_closes)) ]
        
        # Calculate recent BTC volatility
        current_btc_vol = self._stddev(btc_returns, self.vol_period)
        if current_btc_vol is None:
            return None

        # Calculate average BTC volatility over a longer period (e.g., 2x vol_period)
        if len(btc_returns) < 2 * self.vol_period:
            return None
        avg_btc_vol = self._stddev(btc_returns[:-self.vol_period], self.vol_period) # Average of previous period's vol

        if avg_btc_vol is None or avg_btc_vol == 0:
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Condition 1: BTC realized volatility expands abnormally
        is_btc_vol_expanding = current_btc_vol > avg_btc_vol * self.vol_threshold_multiplier

        # Condition 2: BTC is negative
        is_btc_weak = btc_return_pct < -0.5 # BTC 24h return is negative

        # Condition 3: ETH also confirms weakness
        is_eth_weak = eth_return_pct < -0.5 # ETH 24h return is negative

        # Entry logic for short ETH
        if not ctx.has_position():
            if is_btc_vol_expanding and is_btc_weak and is_eth_weak:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "btc_vol_expansion_eth_weakness",
                        "current_btc_vol": current_btc_vol,
                        "avg_btc_vol": avg_btc_vol,
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                    }
                )
        
        # Exit logic: if volatility normalizes, or conditions reverse
        if ctx.has_position() and ctx.position_direction() == "short":
            # Exit if BTC volatility normalizes (drops below threshold)
            if current_btc_vol < avg_btc_vol * (self.vol_threshold_multiplier * 0.8): # Exit threshold is slightly lower
                 self.last_signal_bar = ctx.bar_index
                 return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "btc_volatility_normalizing",
                        "current_btc_vol": current_btc_vol,
                        "avg_btc_vol": avg_btc_vol,
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                    }
                )
            
            # Or if BTC/ETH return pct turn positive (weakness not confirmed)
            if btc_return_pct > 0 or eth_return_pct > 0:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.4,
                    metadata={
                        "reason": "btc_eth_weakness_not_confirmed",
                        "current_btc_vol": current_btc_vol,
                        "avg_btc_vol": avg_btc_vol,
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_signal_bar = ctx.bar_index