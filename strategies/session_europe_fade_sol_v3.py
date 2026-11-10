from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class SessionEuropeFadeSol(Strategy):
    METADATA = {
        "name": "Session Europe Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-4 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 4 # Cooldown after an entry attempt or exit
        self.last_signal_bar_index = -self.entry_cooldown_bars - 1 # Initialize to allow immediate entry
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = 0.0
        self.session_start_bar_index = -1
        self.in_session = False
        self.traded_today = False # To ensure only one trade per day/session
        self.ema_period = 20 # For trend filtering

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Reset session data at the start of a new day (UTC 00:00)
        if ctx.bar.timestamp.hour == 0 and not self.in_session:
            self.traded_today = False

        # Session logic for UTC 7-12 (European morning)
        current_hour = ctx.bar.timestamp.hour
        
        # Start of session
        if current_hour == 7 and not self.in_session:
            self.in_session = True
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            # print(f"Session start at {ctx.bar.timestamp}")
        
        # Update session high/low if within session
        if self.in_session and ctx.bar_index > self.session_start_bar_index:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
        
        # End of session
        if current_hour == 12: # End of 12th hour, next bar is 13:00
            if self.in_session:
                # print(f"Session end at {ctx.bar.timestamp}")
                if ctx.has_position():
                    # Exit any open position at session end
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close})
                self.in_session = False
                self.session_high = -math.inf
                self.session_low = math.inf
                self.session_open = 0.0
                self.session_start_bar_index = -1
        
        # If not in session or already traded today, do nothing for entry
        if not self.in_session or self.traded_today:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_signal_bar_index + self.entry_cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        
        # Do not fade in crisis or extreme volatility
        if regime in ["CRISIS", "MELTDOWN"] or fear_greed < 25: # Extreme fear suggests strong downtrend or crisis
            return None
        
        # Trend filter: Use EMA to filter out strong trends
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Calculate session range and current extension
        session_range = self.session_high - self.session_low
        if session_range == 0: # Avoid division by zero if no range yet
            return None

        # Define "stretched" conditions
        # Entry threshold for fading: e.g., price has moved > 75% of the average daily range from session open
        # Or price is near session high/low after a significant move
        
        # Let's define "stretched" as price being near the session high/low and having moved significantly from the open
        stretch_factor = 0.75 # How much of the session range constitutes a "stretch"
        
        # Long entry: price is near session low, but has moved down significantly, implying a fade up
        # Short entry: price is near session high, but has moved up significantly, implying a fade down
        
        # Condition 1: Price is significantly away from the session open
        move_from_open = ctx.bar.close - self.session_open
        
        # Condition 2: Price is near the extremes of the current session range
        near_high_threshold = self.session_high - (session_range * 0.1)
        near_low_threshold = self.session_low + (session_range * 0.1)

        # Condition 3: Check for "stalling" - e.g., current bar is a reversal candle or small body
        # For simplicity, we'll use price action relative to the session's overall move and EMA
        
        # Avoid fading strong trends (price consistently above/below EMA)
        # We want to fade when price has stretched but is stalling or showing weakness against the trend
        
        signal_confidence = 0.65
        
        # Long signal: Stretched down, near session low, but current price is above EMA or flat/reversing
        if not ctx.has_position() and not self.traded_today:
            # Check for stretched condition and potential reversal/stall
            # Price near session low AND (price significantly below session open OR current bar is a hammer/doji after downtrend)
            # AND price is not strongly below EMA (i.e., not a confirmed downtrend)
            
            # Stretched down: close is within 10% of the session low AND has moved down significantly from open
            if ctx.bar.close <= near_low_threshold and move_from_open < -session_range * stretch_factor:
                # Check if it's not a strong confirmed downtrend (e.g., current close > EMA or close is not far below EMA)
                if ctx.bar.close > ema * 0.995: # Close not significantly below EMA
                    self.last_signal_bar_index = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "europe_session_fade_long",
                            "session_open": self.session_open,
                            "session_low": self.session_low,
                            "session_high": self.session_high,
                            "current_close": ctx.bar.close,
                            "move_from_open_pct": (move_from_open / self.session_open) * 100 if self.session_open else 0,
                            "ema_filter": ema,
                            "fear_greed": fear_greed,
                            "regime": regime
                        }
                    )

            # Short signal: Stretched up, near session high, but current price is below EMA or flat/reversing
            # Stretched up: close is within 10% of the session high AND has moved up significantly from open
            if ctx.bar.close >= near_high_threshold and move_from_open > session_range * stretch_factor:
                # Check if it's not a strong confirmed uptrend (e.g., current close < EMA or close is not far above EMA)
                if ctx.bar.close < ema * 1.005: # Close not significantly above EMA
                    self.last_signal_bar_index = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "europe_session_fade_short",
                            "session_open": self.session_open,
                            "session_low": self.session_low,
                            "session_high": self.session_high,
                            "current_close": ctx.bar.close,
                            "move_from_open_pct": (move_from_open / self.session_open) * 100 if self.session_open else 0,
                            "ema_filter": ema,
                            "fear_greed": fear_greed,
                            "regime": regime
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar_index = ctx.bar_index # Reset cooldown on position close
        # print(f"Position closed: {position['direction']} at {ctx.bar.timestamp}, return: {position['return_bps']} bps")
        # Optional: adaptive logic based on position outcome