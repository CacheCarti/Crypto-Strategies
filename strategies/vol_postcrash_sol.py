from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecovery(Strategy):
    METADATA = {
        "name": "VolatilityRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 60,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.vol_period = 20
        self.cooldown_bars = 10 # bars to wait after a position closes
        self.last_trade_bar_index = -self.cooldown_bars

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
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Calculate Z-score of close price based on recent volatility
        zscore = self._zscore(closes, self.vol_period)
        if zscore is None:
            return None

        # Calculate short-term SMA for stabilization check
        fast_sma = self._sma(closes, self.fast_period)
        if fast_sma is None:
            return None

        # Calculate long-term SMA for trend context
        slow_sma = self._sma(closes, self.slow_period)
        if slow_sma is None:
            return None

        # Entry logic: Look for sharp flush (low Z-score) followed by stabilization
        if not ctx.has_position():
            # Condition 1: Sharp flush (price significantly below its recent mean)
            # A negative z-score indicates price is below the mean.
            # We want it to be significantly below, like -2.0 or lower.
            is_flushed = zscore < -2.0

            # Condition 2: Stabilization check (current close is above fast SMA and fast SMA is rising slightly)
            # This indicates a potential bottoming or initial recovery.
            is_stabilizing = current_close > fast_sma and closes[-1] > closes[-2] # Simple rising fast SMA check

            # Condition 3: Price is below the slow SMA (overall bearish context for recovery play)
            # This ensures we are trying to catch a bounce in a downtrend, not a continuation of an uptrend.
            is_below_slow_sma = current_close < slow_sma

            if is_flushed and is_stabilizing and is_below_slow_sma:
                confidence = min(1.0, abs(zscore) / 3.0) # Confidence scales with flush depth
                self.last_trade_bar_index = ctx.bar_index # Update last trade index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "volatility_recovery_flush_stabilization",
                        "zscore_close": zscore,
                        "current_close": current_close,
                        "fast_sma": fast_sma,
                        "slow_sma": slow_sma,
                    }
                )
        elif ctx.position_direction() == "long":
            # Exit logic (optional, TP/SL handled by engine, but can add conditional exits)
            # For this strategy, we primarily rely on declared SL/TP.
            # If we wanted to add an early exit, e.g., if it goes back below fast_sma:
            # if current_close < fast_sma * 0.99: # 1% buffer
            #     return ctx.signal("flat", confidence=0.7, metadata={"reason": "early_exit_below_fast_sma", "current_close": current_close, "fast_sma": fast_sma})
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can log the outcome or adapt behavior here if needed.
        # For now, just reset the cooldown.
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive sizing here, e.g.:
        # if position["return_bps"] > 0:
        #     self.win_streak += 1
        # else:
        #     self.win_streak = 0
        # print(f"Position closed. Return: {position['return_bps']} bps. Bars held: {position['bars_held']}")
        pass