from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightSessionHandoff(Strategy):
    METADATA = {
        "name": "Overnight Session Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour to 6 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features used beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 6 # overnight session length in hours
        self.entry_cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar_index = -self.entry_cooldown_bars

        # For regime filtering
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.stddev_period = 20
        self.min_volatility_zscore = -0.5 # Only trade if volatility is not too low

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Regime filter: Only trade if not in a very low volatility environment
        # Use z-score of volatility (stddev of returns) to gauge this
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility_zscore = self._zscore([abs(r) for r in returns], self.stddev_period)
        if volatility_zscore is None or volatility_zscore < self.min_volatility_zscore:
            return None # Stand down in low volatility

        # Exit logic
        if ctx.has_position():
            # If position is held past 12 UTC (US session start), or 18 UTC (US session close), exit
            # We want to carry through the handoff, so exit before the *next* major session opens
            if (ctx.position_direction() == "long" and current_hour == 12) or \
               (ctx.position_direction() == "short" and current_hour == 18):
                reason = "session_boundary_exit_handoff"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_hour": current_hour, "price": ctx.bar.close})
            return None

        # Entry logic: Overnight session (UTC 0-6) directional impulse
        # Look for entry between 6-8 UTC to capture handoff confirmation
        if not ctx.has_position() and current_hour >= 6 and current_hour < 8:
            overnight_closes = ctx.closes(self.lookback_period + 1)[-(self.lookback_period + 1):]
            if len(overnight_closes) < self.lookback_period + 1:
                return None

            # Calculate overnight impulse
            overnight_start_price = overnight_closes[0]
            overnight_end_price = overnight_closes[-1]
            price_change = overnight_end_price - overnight_start_price

            # Simple moving average for trend confirmation
            sma_short = self._ema(closes, self.ema_fast_period)
            sma_long = self._ema(closes, self.ema_slow_period)

            if sma_short is None or sma_long is None:
                return None

            # Long entry condition: Positive overnight impulse and short EMA above long EMA
            if price_change > 0 and sma_short > sma_long:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.75,
                                  metadata={
                                      "reason": "overnight_long_impulse_ema_confirm",
                                      "overnight_price_change": price_change,
                                      "current_hour": current_hour,
                                      "sma_short": sma_short,
                                      "sma_long": sma_long,
                                      "volatility_zscore": volatility_zscore
                                  })

            # Short entry condition: Negative overnight impulse and short EMA below long EMA
            elif price_change < 0 and sma_short < sma_long:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.75,
                                  metadata={
                                      "reason": "overnight_short_impulse_ema_confirm",
                                      "overnight_price_change": price_change,
                                      "current_hour": current_hour,
                                      "sma_short": sma_short,
                                      "sma_long": sma_long,
                                      "volatility_zscore": volatility_zscore
                                  })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally reset or adapt state based on trade outcome
        pass