from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructureETH(Strategy):
    METADATA = {
        "name": "VolatilityStructureETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour average hold
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this OHLCV-only strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 40 # Period for trend detection (SMA)
        self.short_period = 10 # Period for recent volatility
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Volatility thresholds
        self.vol_threshold_low = 0.005 # 0.5%
        self.vol_threshold_high = 0.02 # 2%

        # Event boundary lookback
        self.event_boundary_lookback = 5

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + self.event_boundary_lookback + 1)
        highs = ctx.highs(self.event_boundary_lookback + 1)
        lows = ctx.lows(self.event_boundary_lookback + 1)
        volumes = ctx.volumes(self.short_period + 1)

        if len(closes) < self.long_period + self.event_boundary_lookback + 1 or len(highs) < self.event_boundary_lookback + 1 or len(lows) < self.event_boundary_lookback + 1 or len(volumes) < self.short_period + 1:
            return None

        if ctx.bar_index < self.warmup_bars:
            return None

        if ctx.has_position():
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Trend detection (longer-term SMA)
        sma_long = self._sma(closes, self.long_period)
        if sma_long is None:
            return None

        # Recent volatility (ATR proxy using StdDev of closes)
        recent_closes = closes[-self.short_period:]
        stddev_recent = self._stddev(recent_closes, self.short_period)
        if stddev_recent is None:
            return None

        # Calculate percentage volatility
        avg_price_recent = sum(recent_closes) / len(recent_closes)
        percent_volatility = stddev_recent / avg_price_recent if avg_price_recent > 0 else 0.0

        # Regime gate: only trade in 'normal' or 'volatile' regimes and with appropriate volatility
        if ctx.regime == "crisis" or ctx.regime == "calm":
            return None
        
        # Volatility filter for entries
        if not (self.vol_threshold_low < percent_volatility < self.vol_threshold_high):
            return None

        # Check for clean event boundary (no overlapping highs/lows in the last few bars)
        # This simplifies "preserving a sequence" by looking for a clear direction change or pullback start
        is_clean_uptrend_pullback = False
        is_clean_downtrend_pullback = False

        # Look for higher highs and higher lows for an uptrend pullback
        if closes[-1] > sma_long: # Only consider long if above long-term SMA
            is_higher_highs = True
            is_higher_lows = True
            for i in range(1, self.event_boundary_lookback):
                if highs[-i] <= highs[-(i+1)]:
                    is_higher_highs = False
                if lows[-i] <= lows[-(i+1)]:
                    is_higher_lows = False
            
            # A pullback implies recent price is below the peak, but still within trend structure
            # And current close is above the low of the last event_boundary_lookback bars
            if is_higher_highs and is_higher_lows and closes[-1] < highs[-2] and closes[-1] > lows[-self.event_boundary_lookback]:
                is_clean_uptrend_pullback = True

        # Look for lower highs and lower lows for a downtrend pullback
        if closes[-1] < sma_long: # Only consider short if below long-term SMA
            is_lower_highs = True
            is_lower_lows = True
            for i in range(1, self.event_boundary_lookback):
                if highs[-i] >= highs[-(i+1)]:
                    is_lower_highs = False
                if lows[-i] >= lows[-(i+1)]:
                    is_lower_lows = False
            
            # A pullback implies recent price is above the trough, but still within trend structure
            # And current close is below the high of the last event_boundary_lookback bars
            if is_lower_highs and is_lower_lows and closes[-1] > lows[-2] and closes[-1] < highs[-self.event_boundary_lookback]:
                is_clean_downtrend_pullback = True


        # Entry logic
        if is_clean_uptrend_pullback:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "uptrend_pullback_volatility_filtered",
                    "current_price": current_price,
                    "sma_long": sma_long,
                    "percent_volatility": percent_volatility,
                }
            )

        if is_clean_downtrend_pullback:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "downtrend_pullback_volatility_filtered",
                    "current_price": current_price,
                    "sma_long": sma_long,
                    "percent_volatility": percent_volatility,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # Log for debugging/analysis
        # ctx.log_info(f"Position closed: {position['direction']} at {position['exit_price']} (entry: {position['entry_price']}), return: {position['return_bps']} bps, held: {position['bars_held']} bars.")
        pass