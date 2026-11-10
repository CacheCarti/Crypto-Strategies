from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightFade(Strategy):
    METADATA = {
        "name": "Sol Overnight Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max hold until 13:00 UTC next day (6 hours + 13 hours = 19h max)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 10
        self.fade_threshold_pct = 0.5  # Percentage move from close to high/low to consider stretched
        self.max_session_move_std = 1.5 # How many std devs from avg session move is "stretched"
        self.min_confidence = 0.6
        self.cooldown_bars = 6 # Wait 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Define session boundaries
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00 (bar at 06:00 is the last bar of this session)
        self.exit_before_hour = 13     # UTC 13:00 (exit before this bar starts)

        # Store session data
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_start_bar_index = None

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + 1)
        highs = ctx.highs(self.long_period + 1)
        lows = ctx.lows(self.long_period + 1)

        if len(closes) < self.long_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Daily session management
        if current_hour == self.overnight_start_hour and ctx.bar_index != self.session_start_bar_index:
            # New overnight session starts
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
        elif self.session_open_price is not None:
            # Update session high/low within the session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic for open positions
        if ctx.has_position():
            # Exit if session is ending or exit hour is reached
            if current_hour == self.exit_before_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_exit_time_reached",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_before_hour
                })

            # Check for stop-loss or take-profit on open position
            # This is primarily handled by the engine's declared SL/TP,
            # but we can add adaptive logic here if needed.
            # For this strategy, we rely on the engine's declared SL/TP and the time-based exit.
            return ctx.signal("hold")

        # Entry logic (only if no position and cooldown passed)
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider entries within the overnight session
        if not (self.overnight_start_hour <= current_hour <= self.overnight_end_hour):
            return None

        if self.session_open_price is None: # Session not properly initialized
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Do not trade in crisis or strong trends
        if regime in ["CRISIS", "MELTDOWN"]:
            return None
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None
        if fear_greed < 30 or fear_greed > 70: # Avoid extreme fear/greed
            return None

        # Calculate current session move
        session_range = self.session_high - self.session_low
        session_move_from_open = current_close - self.session_open_price
        session_move_from_low = current_close - self.session_low
        session_move_from_high = current_close - self.session_high

        # Check for stretched condition
        is_stretched_up = (current_close > self.session_open_price and
                           (current_close - self.session_open_price) / self.session_open_price * 100 > self.fade_threshold_pct and
                           (self.session_high - current_close) / session_range < 0.2 if session_range > 0 else True) # Close to session high
        
        is_stretched_down = (current_close < self.session_open_price and
                             (self.session_open_price - current_close) / self.session_open_price * 100 > self.fade_threshold_pct and
                             (current_close - self.session_low) / session_range < 0.2 if session_range > 0 else True) # Close to session low

        # Additional condition: current bar's close is significantly different from previous bar's close
        # This helps identify the "stall" or "excess" after a run
        prev_close = closes[-2]
        current_bar_move_pct = (current_close - prev_close) / prev_close * 100

        # Fade logic:
        # If stretched up and current bar shows reversal/stall, go short
        if is_stretched_up and current_bar_move_pct < 0: # Current bar is down after an up move
            confidence = self.min_confidence + (0.9 - self.min_confidence) * (1 - abs(funding_rate * 100)) # Higher confidence if funding is less extreme
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=confidence, metadata={
                "reason": "overnight_fade_stretched_up_stall",
                "session_open": self.session_open_price,
                "current_close": current_close,
                "session_move_pct": (session_move_from_open / self.session_open_price) * 100,
                "current_bar_move_pct": current_bar_move_pct,
                "regime": regime,
                "trend_regime": trend_regime,
                "fear_greed": fear_greed,
                "funding_rate": funding_rate
            })

        # If stretched down and current bar shows reversal/stall, go long
        if is_stretched_down and current_bar_move_pct > 0: # Current bar is up after a down move
            confidence = self.min_confidence + (0.9 - self.min_confidence) * (1 - abs(funding_rate * 100)) # Higher confidence if funding is less extreme
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=confidence, metadata={
                "reason": "overnight_fade_stretched_down_stall",
                "session_open": self.session_open_price,
                "current_close": current_close,
                "session_move_pct": (session_move_from_open / self.session_open_price) * 100,
                "current_bar_move_pct": current_bar_move_pct,
                "regime": regime,
                "trend_regime": trend_regime,
                "fear_greed": fear_greed,
                "funding_rate": funding_rate
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # You can add logging or adaptive logic here.
        # For example, adjust confidence based on recent performance.
        pass