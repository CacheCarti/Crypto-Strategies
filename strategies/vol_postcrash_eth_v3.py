from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "Volatility Recovery Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # ~3 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features used, only OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 10  # Bars to detect a sharp flush
        self.stabilization_period = 5 # Bars to confirm stabilization after a flush
        self.recovery_period = 15 # Bars to check for recovery drift
        self.min_flush_magnitude_pct = 1.0 # Minimum % drop for a flush
        self.max_stabilization_volatility_pct = 0.5 # Max % range for stabilization
        self.min_recovery_drift_pct = 0.2 # Min % drift for recovery
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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

        current_price = ctx.bar.close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for sharp flush
        flush_closes = ctx.closes(self.flush_period + 1)
        if len(flush_closes) < self.flush_period + 1:
            return None
        
        # Calculate percentage drop over the flush period
        flush_start_price = flush_closes[0]
        flush_end_price = flush_closes[-1]
        price_drop_pct = (flush_start_price - flush_end_price) / flush_start_price * 100

        # Check if it was a significant flush AND the current price is near the low of the flush
        # This prevents trading on ongoing drops
        flush_low = min(lows[-self.flush_period:])
        is_flush = price_drop_pct >= self.min_flush_magnitude_pct and current_price <= flush_low * 1.005 # current price within 0.5% of flush low

        if not is_flush:
            return None

        # Check for stabilization after the flush
        stabilization_highs = ctx.highs(self.stabilization_period)
        stabilization_lows = ctx.lows(self.stabilization_period)
        stabilization_closes = ctx.closes(self.stabilization_period)

        if len(stabilization_highs) < self.stabilization_period or len(stabilization_lows) < self.stabilization_period:
            return None

        stabilization_range_pct = (max(stabilization_highs) - min(stabilization_lows)) / min(stabilization_lows) * 100
        
        # Ensure current price is within the stabilization range
        is_stabilized = stabilization_range_pct <= self.max_stabilization_volatility_pct and \
                        current_price >= min(stabilization_lows) and \
                        current_price <= max(stabilization_highs)

        if not is_stabilized:
            return None

        # Check for recovery drift
        recovery_closes = ctx.closes(self.recovery_period)
        if len(recovery_closes) < self.recovery_period:
            return None

        recovery_start_price = recovery_closes[0]
        recovery_end_price = recovery_closes[-1]
        recovery_drift_pct = (recovery_end_price - recovery_start_price) / recovery_start_price * 100

        is_recovering = recovery_drift_pct >= self.min_recovery_drift_pct
        
        # If all conditions met, go long
        if is_flush and is_stabilized and is_recovering and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            confidence = min(1.0, (price_drop_pct / self.min_flush_magnitude_pct) * 0.5 + (recovery_drift_pct / self.min_recovery_drift_pct) * 0.5)
            
            # Adjust confidence based on market regime
            if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
                confidence *= 0.7 # Be more cautious in extreme regimes
            elif ctx.market.get("regime", "NORMAL") == "HIGH_VOL":
                confidence *= 0.9 # Slightly more cautious
            
            confidence = max(0.1, min(0.9, confidence)) # Ensure confidence is within reasonable bounds

            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "volatility_recovery_drift",
                    "flush_magnitude_pct": round(price_drop_pct, 2),
                    "stabilization_range_pct": round(stabilization_range_pct, 2),
                    "recovery_drift_pct": round(recovery_drift_pct, 2),
                    "current_price": current_price,
                    "regime": ctx.market.get("regime", "NORMAL")
                }
            )

        # If we have a position, and the recovery seems to be failing or reversing, consider exiting
        if ctx.has_position() and ctx.position_direction() == "long":
            # Check for significant reversal from peak or failure to continue recovery
            # Simple check: if current price drops below the average of the last few stabilization bars
            # or if it drops significantly from its recent high
            
            # Re-evaluate stabilization
            current_stabilization_avg = self._sma(stabilization_closes, self.stabilization_period)
            
            # Exit if price falls below the stabilization average or if it's been held too long without significant profit
            if current_stabilization_avg is not None and current_price < current_stabilization_avg * 0.995: # 0.5% below stabilization avg
                 self.last_trade_bar = ctx.bar_index
                 return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "recovery_fading_exit",
                        "current_price": current_price,
                        "stabilization_avg": round(current_stabilization_avg, 2),
                        "bars_held": ctx.bar_index - self.last_trade_bar # Approximate bars held
                    }
                )


        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to ensure cooldown is applied after a closure
        self.last_trade_bar = ctx.bar_index
        # Log or adjust strategy based on position outcome
        # For simplicity, no adaptive logic implemented here, but could be used to adjust parameters
        pass