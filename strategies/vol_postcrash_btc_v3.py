from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "Volatility Recovery Drift BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for several hours to half a day hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 10  # Bars to detect a sharp flush
        self.flush_threshold_pct = -1.5  # -1.5% drop in flush_period bars
        self.stabilization_period = 5  # Bars to wait for stabilization after flush
        self.recovery_period = 10 # Bars for recovery drift
        self.cooldown_bars = 20  # Cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Condition 1: Detect a sharp flush (significant price drop)
        flush_closes = ctx.closes(self.flush_period + 1)
        if len(flush_closes) < self.flush_period + 1:
            return None
        
        flush_start_price = flush_closes[0]
        flush_end_price = flush_closes[-1]
        
        # Check if the price has dropped significantly within the flush period
        flush_return_pct = ((flush_end_price - flush_start_price) / flush_start_price) * 100
        
        # Check for a sharp drop AND that the current bar is near the low of the flush period
        # This helps ensure we're catching the "bottom" of the flush, not just a slow bleed
        recent_low = min(lows[-self.flush_period:])
        if flush_return_pct < self.flush_threshold_pct and ctx.bar.low <= recent_low * 1.001: # current low is very near the recent low
            
            # Condition 2: Stabilization after the flush
            # Look for reduced volatility or sideways movement in the stabilization period
            # Use standard deviation of returns for volatility, or range
            stabilization_closes = ctx.closes(self.stabilization_period)
            if len(stabilization_closes) < self.stabilization_period:
                return None
            
            # Check if the price has not dropped further and is relatively stable
            # Example: current close is above the low of the stabilization period
            # And the range of the stabilization period is relatively small
            stabilization_low = min(lows[-self.stabilization_period:])
            stabilization_high = max(highs[-self.stabilization_period:])
            stabilization_range_pct = ((stabilization_high - stabilization_low) / stabilization_low) * 100

            # Price must not have dropped much further and range should be contained
            if ctx.bar.close >= stabilization_low and stabilization_range_pct < abs(self.flush_threshold_pct / 2): # Range is less than half the flush magnitude
                
                # Condition 3: Recovery drift (price starting to move up)
                # Check if the current close is above the average of the stabilization period
                stabilization_sma = self._sma(stabilization_closes, self.stabilization_period)
                if stabilization_sma is not None and ctx.bar.close > stabilization_sma:
                    
                    # Ensure we are not already in a position
                    if not ctx.has_position():
                        self.last_signal_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "volatility_recovery_drift",
                                "flush_return_pct": flush_return_pct,
                                "stabilization_range_pct": stabilization_range_pct,
                                "current_close": ctx.bar.close,
                                "stabilization_sma": stabilization_sma,
                            }
                        )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar index to allow for cooldown
        # This is implicitly handled by `self.last_signal_bar_index = ctx.bar_index` in on_bar
        # if a signal is generated, and not explicitly reset here.
        # We can add adaptive logic here if needed, e.g., adjust confidence based on recent performance.
        pass