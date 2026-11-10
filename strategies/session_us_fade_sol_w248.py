from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionFade(Strategy):
    METADATA = {
        "name": "SOL US Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,       # Stop-loss at 2%
        "declared_tp_bps": 400.0,       # Take-profit at 4%
        "declared_hold_seconds": 3600 * 4, # Intended avg hold time (up to 4 hours within session)
        "warmup_bars": 30,              # Need enough bars for EMA and range calculations
        "required_features": [],        # No external features needed for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13  # UTC 13:00 (US session open)
        self.session_close_hour = 21 # UTC 21:00 (US session close, exit before this)
        self.session_range_period = 8 # Bars for session range calculation
        self.ema_period = 20 # EMA for trend filtering
        self.max_stretch_factor = 1.5 # How many ATRs away from open is "stretched"
        self.min_stretch_factor = 0.5 # Minimum stretch to consider fading
        self.atr_period = 14
        self.cooldown_bars = 4 # Cooldown after a trade
        
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        # State variables for session logic
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_open_price = -1.0
        self.session_start_bar = -1
        self.session_midpoint = -1.0


    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period):
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

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Calculate EMA for trend filtering
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Calculate ATR for volatility scaling
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None:
            return None

        # --- Session Management ---
        is_session_start = (current_hour == self.session_open_hour and ctx.bar_index != self.session_start_bar)
        is_session_active = (current_hour >= self.session_open_hour and current_hour < self.session_close_hour)
        is_session_end_bar = (current_hour == self.session_close_hour -1 ) # Bar before session close

        # Reset session state at the start of a new session
        if is_session_start:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open_price = ctx.bar.open
            self.session_start_bar = ctx.bar_index
            self.session_midpoint = self.session_open_price # Will be updated as session progresses
            # If a position is open from previous session, flat it
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_reset", "current_price": current_price})

        # Update session high/low if active
        if is_session_active and ctx.bar_index > self.session_start_bar:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            # Update session midpoint based on current range
            if self.session_high > self.session_low:
                self.session_midpoint = (self.session_high + self.session_low) / 2.0
            else: # Fallback if range is 0
                self.session_midpoint = self.session_open_price

        # --- Exit Logic (if position is open) ---
        if ctx.has_position():
            # Exit before session close
            if is_session_end_bar:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_price": current_price, "session_midpoint": self.session_midpoint})
            
            # If price returns to midpoint, consider taking profit if already profitable
            if ctx.position_direction() == "long" and current_price >= ctx.position["entry_price"] and current_price > self.session_midpoint and current_price >= ctx.position["entry_price"] + 0.5 * atr_val:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_fade_target_hit", "current_price": current_price, "session_midpoint": self.session_midpoint, "entry_price": ctx.position["entry_price"]})
            if ctx.position_direction() == "short" and current_price <= ctx.position["entry_price"] and current_price < self.session_midpoint and current_price <= ctx.position["entry_price"] - 0.5 * atr_val:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_fade_target_hit", "current_price": current_price, "session_midpoint": self.session_midpoint, "entry_price": ctx.position["entry_price"]})

            return ctx.signal("hold") # Hold existing position if no exit condition met

        # --- Entry Logic (if no position is open) ---
        if not is_session_active:
            return None # Only trade during US session

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entries after a few bars into the session
        if ctx.bar_index - self.session_start_bar < 3:
            return None

        # Don't trade in crisis or highly volatile regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "HIGH_VOL"]:
            return None

        # Calculate current stretch from session open
        current_stretch = (current_price - self.session_open_price) / atr_val if atr_val > 0 else 0

        # Determine if the session move is "stretched"
        is_stretched_long = current_stretch > self.max_stretch_factor
        is_stretched_short = current_stretch < -self.max_stretch_factor

        # Determine if there's a stall (e.g., price pulling back slightly from extreme)
        # For long fade (short entry): price made a high and is now slightly off it
        stall_for_short = is_stretched_long and current_price < ctx.highs(1)[0]
        # For short fade (long entry): price made a low and is now slightly off it
        stall_for_long = is_stretched_short and current_price > ctx.lows(1)[0]

        # --- Fade Logic ---
        # Fading a stretched long move (entering short)
        if stall_for_short and current_price > self.session_open_price: # Ensure we are above open
            # Only fade if not in a strong bull trend
            if current_price < ema_val: # Price is below EMA, suggesting weakness or pullback
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75,
                                  metadata={
                                      "reason": "session_fade_short",
                                      "current_price": current_price,
                                      "session_open": self.session_open_price,
                                      "session_high": self.session_high,
                                      "session_midpoint": self.session_midpoint,
                                      "stretch_factor": current_stretch,
                                      "atr": atr_val,
                                      "ema": ema_val
                                  })

        # Fading a stretched short move (entering long)
        if stall_for_long and current_price < self.session_open_price: # Ensure we are below open
            # Only fade if not in a strong bear trend
            if current_price > ema_val: # Price is above EMA, suggesting strength or pullback
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75,
                                  metadata={
                                      "reason": "session_fade_long",
                                      "current_price": current_price,
                                      "session_open": self.session_open_price,
                                      "session_low": self.session_low,
                                      "session_midpoint": self.session_midpoint,
                                      "stretch_factor": current_stretch,
                                      "atr": atr_val,
                                      "ema": ema_val
                                  })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Placeholder for adaptive logic or logging. Not used in this strategy.
        pass