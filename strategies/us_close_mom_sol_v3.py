from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USCloseMomentumSOL(Strategy):
    METADATA = {
        "name": "USCloseMomentumSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for a 2-hour hold, exit by UTC 0
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, using OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.momentum_period = 4
        self.min_range_multiplier = 1.0 # Minimum range relative to average
        self.min_volume_multiplier = 1.0 # Minimum volume relative to average
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _average_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for open positions
        if ctx.has_position():
            # Exit at UTC 0 (end of day) or if it's held too long
            if current_hour == 0 or ctx.bar_index - self.last_trade_bar > 4: # Max 4-hour hold
                direction = ctx.position_direction()
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "time_based_exit", "exit_hour": current_hour, "bars_held": ctx.bar_index - self.last_trade_bar})
            return None # Hold position

        # Entry logic: Only consider entries around US market close (UTC 21-22)
        if not (current_hour >= 21 and current_hour < 22):
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators for momentum
        # Look at the last `momentum_period` bars (e.g., 4 hours before close)
        momentum_closes = closes[-self.momentum_period:]
        momentum_highs = highs[-self.momentum_period:]
        momentum_lows = lows[-self.momentum_period:]
        momentum_volumes = volumes[-self.momentum_period:]

        if len(momentum_closes) < self.momentum_period:
            return None

        # Calculate average range and volume over a longer period for comparison
        avg_range_long = self._average_true_range(highs, lows, closes, 20)
        avg_volume_long = self._sma(volumes, 20)

        if avg_range_long is None or avg_volume_long is None:
            return None

        # Momentum calculation: simple directional movement and strength
        # Check if the last bar's close is significantly higher/lower than the start of the momentum window
        # And if the last bar itself showed strong directional movement
        
        # Long condition
        # Price must have moved up significantly over the momentum period
        # And the last bar must have a larger than average range and volume
        if closes[-1] > closes[-self.momentum_period] * (1 + 0.005 * self.momentum_period) and \
           (highs[-1] - lows[-1]) > avg_range_long * self.min_range_multiplier and \
           volumes[-1] > avg_volume_long * self.min_volume_multiplier:
            
            # Additional filter: ensure price is above a short-term EMA
            fast_ema = self._ema(closes, self.fast_ema_period)
            if fast_ema is not None and closes[-1] > fast_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "us_close_strong_long_momentum",
                        "close_price": closes[-1],
                        "momentum_start_price": closes[-self.momentum_period],
                        "current_range": (highs[-1] - lows[-1]),
                        "avg_range_20": avg_range_long,
                        "current_volume": volumes[-1],
                        "avg_volume_20": avg_volume_long,
                        "fast_ema": fast_ema
                    }
                )

        # Short condition
        # Price must have moved down significantly over the momentum period
        # And the last bar must have a larger than average range and volume
        elif closes[-1] < closes[-self.momentum_period] * (1 - 0.005 * self.momentum_period) and \
             (highs[-1] - lows[-1]) > avg_range_long * self.min_range_multiplier and \
             volumes[-1] > avg_volume_long * self.min_volume_multiplier:

            # Additional filter: ensure price is below a short-term EMA
            fast_ema = self._ema(closes, self.fast_ema_period)
            if fast_ema is not None and closes[-1] < fast_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "us_close_strong_short_momentum",
                        "close_price": closes[-1],
                        "momentum_start_price": closes[-self.momentum_period],
                        "current_range": (highs[-1] - lows[-1]),
                        "avg_range_20": avg_range_long,
                        "current_volume": volumes[-1],
                        "avg_volume_20": avg_volume_long,
                        "fast_ema": fast_ema
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position close
        self.last_trade_bar = ctx.bar_index
        # Optional: adaptive sizing based on last trade performance
        # For simplicity, not implementing adaptive sizing in this version
        pass