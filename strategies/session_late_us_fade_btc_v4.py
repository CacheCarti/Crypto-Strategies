from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LateUSSessionFade(Strategy):
    METADATA = {
        "name": "Late US Session Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar, might hold longer
        "warmup_bars": 50, # Need enough bars for 20-period calculations and session tracking
        "required_features": [], # No external features needed for this price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session)
        self.session_end_hour = 23   # UTC 23:00 (end of late US session, before UTC 0)
        self.cooldown_bars = 3       # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Parameters for session stretch detection
        self.stretch_period = 6 # Bars to calculate session stretch (6 hours = 6 bars)
        self.stretch_std_multiplier = 1.5 # How many stddevs from mean to consider stretched

        # Parameters for trend/volatility filtering
        self.trend_filter_period = 20 # For moving average
        self.volatility_filter_period = 20 # For ATR

        # Store session data
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open = -1.0
        self.session_close_price = -1.0
        self.session_bars = [] # Store closes for stddev calc
        self.session_midpoint = -1.0
        self.session_initial_bar_index = -1

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # --- Cooldown check ---
        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None

        # --- Session Tracking ---
        is_new_session = current_hour == self.session_start_hour and self.session_initial_bar_index != ctx.bar_index
        
        if is_new_session:
            # Reset session data for a new day
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_close_price = ctx.bar.close
            self.session_bars = [ctx.bar.close]
            self.session_midpoint = (ctx.bar.high + ctx.bar.low) / 2
            self.session_initial_bar_index = ctx.bar_index
        elif current_hour >= self.session_start_hour and current_hour <= self.session_end_hour:
            # Update session data
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close_price = ctx.bar.close
            self.session_bars.append(ctx.bar.close)
            # Update midpoint based on full session range so far
            self.session_midpoint = (self.session_high + self.session_low) / 2
        else:
            # Not within the session, do nothing for session tracking
            # Also, if we have an open position, we might want to exit it before UTC 0
            if ctx.has_position() and ctx.bar.timestamp.hour == 0: # End of day, close position
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "eod_session_close",
                    "current_price": ctx.bar.close,
                    "exit_hour": ctx.bar.timestamp.hour
                })
            return None # Not in session, no signal

        # --- Entry Logic (within session_start_hour to session_end_hour) ---
        if not ctx.has_position() and \
           current_hour >= self.session_start_hour + 2 and \
           current_hour <= self.session_end_hour: # Give session some time to develop (e.g., 2 hours in)

            # Regime filters: avoid strong trends and crisis
            # Trend filter: ensure not in a strong trend
            sma_fast = self._sma(closes, self.trend_filter_period // 2)
            sma_slow = self._sma(closes, self.trend_filter_period)
            if sma_fast is None or sma_slow is None:
                return None
            
            # If fast SMA is too far from slow SMA, it indicates a strong trend, stand down
            # Using ATR to normalize the difference for volatility
            current_atr = self._atr(highs, lows, closes, self.volatility_filter_period)
            if current_atr is None or current_atr == 0:
                return None
            
            # Trend strength as a percentage of ATR
            trend_strength_atr = abs(sma_fast - sma_slow) / current_atr
            if trend_strength_atr > 1.0: # If trend is strong (e.g., > 1 ATR difference)
                # print(f"DEBUG: Skipping due to strong trend. ATR difference: {trend_strength_atr}")
                return None
            
            # Crisis regime filter
            # Use ctx.market.get("regime") or "crisis_score"
            if ctx.market.get("regime", "NORMAL") == "CRISIS" or ctx.market.get("crisis_score", 0.0) > 0.5:
                # print(f"DEBUG: Skipping due to crisis regime. Crisis Score: {ctx.market.get('crisis_score')}")
                return None

            # Session Stretch Detection
            if len(self.session_bars) < self.stretch_period:
                return None
            
            session_moves = [self.session_bars[i] - self.session_bars[i-1] for i in range(1, len(self.session_bars))]
            if len(session_moves) < self.stretch_period -1 :
                return None
            
            avg_move = sum(session_moves[-self.stretch_period+1:]) / (self.stretch_period - 1) if self.stretch_period > 1 else 0
            std_move = self._stddev(session_moves, self.stretch_period -1)
            
            if std_move is None or std_move == 0:
                return None

            current_move = ctx.bar.close - closes[-2] # Current bar's move relative to previous bar's close

            is_stretched_long = (current_move > avg_move + self.stretch_std_multiplier * std_move) and (ctx.bar.close > self.session_midpoint)
            is_stretched_short = (current_move < avg_move - self.stretch_std_multiplier * std_move) and (ctx.bar.close < self.session_midpoint)

            confidence = 0.6 # Base confidence

            # Fade a stretched session move towards the session midpoint
            if is_stretched_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "session_stretch_fade_short",
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": self.session_midpoint,
                    "current_close": ctx.bar.close,
                    "current_move": current_move,
                    "avg_move": avg_move,
                    "std_move": std_move,
                    "trend_strength_atr": trend_strength_atr
                })
            elif is_stretched_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "session_stretch_fade_long",
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": self.session_midpoint,
                    "current_close": ctx.bar.close,
                    "current_move": current_move,
                    "avg_move": avg_move,
                    "std_move": std_move,
                    "trend_strength_atr": trend_strength_atr
                })

        # --- Exit Logic ---
        # If we have a position and it's approaching session end or midpoint
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            
            # Exit if price crosses the session midpoint (target)
            if pos_direction == "long" and ctx.bar.close > self.session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midpoint_target_hit_long",
                    "session_midpoint": self.session_midpoint,
                    "current_close": ctx.bar.close
                })
            elif pos_direction == "short" and ctx.bar.close < self.session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midpoint_target_hit_short",
                    "session_midpoint": self.session_midpoint,
                    "current_close": ctx.bar.close
                })

            # Forced exit before UTC 0 to avoid holding overnight
            if current_hour == self.session_end_hour + 1: # One hour after session end
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "eod_session_close",
                    "current_price": ctx.bar.close,
                    "exit_hour": ctx.bar.timestamp.hour
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: reset session data if a position closed, or log for analysis
        pass