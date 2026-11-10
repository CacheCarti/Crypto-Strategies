from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFade(Strategy):
    METADATA = {
        "name": "US Session Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1-hour bar duration
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00
        self.us_session_duration_bars = self.us_session_end_hour - self.us_session_start_hour
        self.entry_cooldown_bars = 6     # Cooldown after a trade
        self.last_signal_bar_index = -self.entry_cooldown_bars - 100 # Initialize far in the past

        # Periods for indicators
        self.ema_period_short = 10
        self.ema_period_long = 30
        self.rsi_period = 7
        self.zscore_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate average gain and loss over the period
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar_index < self.entry_cooldown_bars:
            return None

        # Calculate indicators
        current_close = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        ema_short = self._ema(closes, self.ema_period_short)
        ema_long = self._ema(closes, self.ema_period_long)
        zscore_close = self._zscore(closes, self.zscore_period)

        if rsi is None or ema_short is None or ema_long is None or zscore_close is None:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Do not fade strong trends or crisis regimes
        if regime in ["CRISIS", "MELTDOWN"]:
            return None
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None
        if fear_greed_index < 20 or fear_greed_index > 80: # Avoid extreme fear/greed
            return None

        # Logic for exiting existing positions
        if ctx.has_position():
            # Exit position if session is ending soon (within 1 hour)
            # or if price crosses back over the session midpoint (approximated by EMA)
            if current_hour >= self.us_session_end_hour - 1: # Exit before session close
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour, "price": current_close})
            
            # If long and price drops below short EMA, consider exiting
            if ctx.position_direction() == "long" and current_close < ema_short:
                 self.last_signal_bar_index = ctx.bar_index
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "fade_long_ema_cross_exit", "current_close": current_close, "ema_short": ema_short})
            
            # If short and price rises above short EMA, consider exiting
            if ctx.position_direction() == "short" and current_close > ema_short:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "fade_short_ema_cross_exit", "current_close": current_close, "ema_short": ema_short})

            return ctx.signal("hold") # Hold if no exit conditions met

        # Entry logic: Only enter during US session (UTC 13-21)
        if not (self.us_session_start_hour <= current_hour < self.us_session_end_hour):
            return None

        # Calculate session move and midpoint
        # We need the open of the current session start bar for this.
        # This requires storing session_open or calculating it from a longer history of opens.
        # For simplicity, we'll use the EMA_long as a proxy for the 'midpoint' of recent action.
        # A more robust implementation would track the actual session open.
        
        # Condition for "unusually stretched and stalls"
        # Using Z-score for stretch and RSI for stall/reversal
        
        signal = None
        confidence = 0.0
        reason = ""
        
        # Long Entry: Price is significantly below EMA_long (stretched down), RSI is oversold and turning up
        if zscore_close < -1.5 and rsi < 30 and current_close > ema_short and ema_short > ema_long:
            # Check for recent price action trying to reverse
            # e.g., current_close > closes[-2] (current close higher than previous)
            if current_close > closes[-2]: # Simple confirmation of upward movement
                signal = "long"
                confidence = 0.75
                reason = "us_session_fade_long_zscore_rsi_ema_bounce"
        
        # Short Entry: Price is significantly above EMA_long (stretched up), RSI is overbought and turning down
        elif zscore_close > 1.5 and rsi > 70 and current_close < ema_short and ema_short < ema_long:
            # Check for recent price action trying to reverse
            # e.g., current_close < closes[-2] (current close lower than previous)
            if current_close < closes[-2]: # Simple confirmation of downward movement
                signal = "short"
                confidence = 0.75
                reason = "us_session_fade_short_zscore_rsi_ema_bounce"

        if signal:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "price": current_close,
                    "rsi": rsi,
                    "zscore_close": zscore_close,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed_index,
                    "current_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset state
        # For this strategy, the cooldown logic is handled in on_bar
        pass