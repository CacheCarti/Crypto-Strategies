from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionUSImpulseBTC(Strategy):
    METADATA = {
        "name": "Session US Impulse BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 8,  # Roughly one session
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this simple version
    }

    def initialize(self, ctx: BarContext) -> None:
        # Parameters for the strategy
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00 (exit before this hour ends)
        self.impulse_period = 4          # Bars to measure impulse (4 hours)
        self.min_impulse_stddev = 0.5    # Minimum impulse strength in stddevs
        self.cooldown_bars = 6           # Cooldown period after a trade (6 hours)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Indicators periods
        self.sma_period_fast = 20
        self.sma_period_slow = 50
        self.stddev_period = 20 # For volatility filter

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
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Regime filters
        # Only trade during "normal" or "volatile" regimes, avoid "crisis"
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
             if ctx.has_position():
                 return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit", "current_regime": ctx.market.get("regime")})
             return None

        # Volatility filter: only trade if recent volatility is not too low
        stddev = self._stddev(closes, self.stddev_period)
        if stddev is None:
            return None # Not enough data for stddev
        
        # We need a reference for what "low" volatility means. Let's use a simple threshold.
        # This threshold might need tuning for BTC 1H. For now, a relative measure.
        # If stddev is too low (e.g., less than 0.1% of price), stand down.
        if stddev < (ctx.bar.close * 0.0005): # Less than 0.05% price movement over period
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "low_volatility_exit", "stddev": stddev})
            return None

        # Exit logic
        if ctx.has_position():
            # If current hour is past the US session end, close position
            if current_hour >= self.us_session_end_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_boundary_exit", "current_hour": current_hour, "entry_hour": ctx.position["metadata"].get("entry_hour")})
            
            # If trend has significantly turned against us (e.g., fast SMA crosses below slow SMA for a long position)
            fast_sma = self._sma(closes, self.sma_period_fast)
            slow_sma = self._sma(closes, self.sma_period_slow)

            if fast_sma and slow_sma:
                if ctx.position_direction() == "long" and fast_sma < slow_sma and closes[-1] < slow_sma:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "trend_reversal_exit_long", "fast_sma": fast_sma, "slow_sma": slow_sma})
                elif ctx.position_direction() == "short" and fast_sma > slow_sma and closes[-1] > slow_sma:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "trend_reversal_exit_short", "fast_sma": fast_sma, "slow_sma": slow_sma})

            return ctx.signal("hold") # Hold if no exit condition met

        # Entry logic
        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only look for entry during the US session start hours
        if current_hour == self.us_session_start_hour:
            # Check if we have enough bars for impulse calculation
            if len(closes) < self.impulse_period + 1:
                return None

            # Measure impulse over the last 'impulse_period' bars
            price_change = closes[-1] - closes[-self.impulse_period-1]
            
            # Calculate standard deviation of recent prices for normalization
            # Using the same stddev from volatility filter for consistency
            if stddev is None or stddev == 0:
                return None

            impulse_zscore = price_change / (stddev * math.sqrt(self.impulse_period)) # Normalize by period's stddev

            # Check for significant impulse
            signal_fired = False
            direction = None
            confidence = 0.0
            reason = ""

            if impulse_zscore > self.min_impulse_stddev:
                direction = "long"
                confidence = min(0.7 + (impulse_zscore - self.min_impulse_stddev) * 0.1, 0.9)
                reason = "us_session_strong_bullish_impulse"
                signal_fired = True
            elif impulse_zscore < -self.min_impulse_stddev:
                direction = "short"
                confidence = min(0.7 + (-impulse_zscore - self.min_impulse_stddev) * 0.1, 0.9)
                reason = "us_session_strong_bearish_impulse"
                signal_fired = True

            if signal_fired:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "impulse_zscore": impulse_zscore,
                        "price_change": price_change,
                        "session_start_hour": self.us_session_start_hour,
                        "current_regime": ctx.market.get("regime"),
                        "stddev": stddev,
                        "entry_hour": current_hour
                    }
                )

        return None