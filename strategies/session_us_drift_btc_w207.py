from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class USSessionBreakout(Strategy):
    METADATA = {
        "name": "US Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour hold, allow for next session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13 # UTC 13:00 (9 AM EST)
        self.session_end_hour = 21   # UTC 21:00 (5 PM EST) - exit before this
        self.cooldown_bars = 5       # Bars to wait after a trade closes
        self.last_trade_bar_index = -self.cooldown_bars
        self.min_range_period = 24   # Bars for median range calculation (approx 1 day)
        self.breakout_multiplier = 1.2 # How much bigger the session move must be than median range
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.rsi_period = 14
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = 0.0
        self.session_volume = 0.0
        self.session_start_bar_index = -1
        self.session_active = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[-(period*2):-period], period) # Use SMA for initial EMA
        if ema is None:
            ema = sum(values[-period:]) / period # Fallback if not enough history for initial SMA
        
        for v in values[-(period):]: # Start EMA from the period's worth of data
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
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Calculate average true range for median range filter
        daily_ranges = []
        for i in range(1, min(len(highs), self.min_range_period + 1)):
            daily_ranges.append(highs[-i] - lows[-i])
        
        if not daily_ranges:
            median_daily_range = 0.0
        else:
            daily_ranges.sort()
            median_daily_range = daily_ranges[len(daily_ranges) // 2]
        
        min_decisive_move = median_daily_range * self.breakout_multiplier

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            if ctx.has_position(): # Allow holding position during cooldown
                return ctx.signal("hold")
            return None

        # --- Session Management ---
        # Start new session
        if current_hour == self.session_start_hour and not self.session_active:
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_volume = ctx.bar.volume
            self.session_start_bar_index = ctx.bar_index
            self.session_active = True
            # Reset session state
            return None

        # Update session stats if active
        if self.session_active and ctx.bar_index > self.session_start_bar_index:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_volume += ctx.bar.volume

        # Exit position if it's nearing session end (e.g. 1 hour before session_end_hour)
        if ctx.has_position() and current_hour >= (self.session_end_hour - 1): # Exit an hour before session end
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # --- Entry Logic ---
        if self.session_active and current_hour > self.session_start_hour and current_hour < self.session_end_hour:
            # Only consider breakout if it's not the first bar of the session
            if ctx.bar_index > self.session_start_bar_index:
                # Calculate indicators
                ema_fast = self._ema(closes, self.ema_fast_period)
                ema_slow = self._ema(closes, self.ema_slow_period)
                rsi = self._rsi(closes, self.rsi_period)

                if ema_fast is None or ema_slow is None or rsi is None:
                    return None

                # Breakout conditions
                long_breakout = current_price > self.session_high and (current_price - self.session_open) > min_decisive_move
                short_breakout = current_price < self.session_low and (self.session_open - current_price) > min_decisive_move

                # Avoid trading if already in position
                if ctx.has_position():
                    return ctx.signal("hold")

                # Filters
                # Fear & Greed Index
                fear_greed = ctx.features.get("fear_greed_index", 50)
                # We want to trade with the trend, so avoid extreme fear for longs and extreme greed for shorts
                
                # Long Signal
                if long_breakout and ema_fast > ema_slow and rsi < 70 and fear_greed > 30:
                    self.last_trade_bar_index = ctx.bar_index
                    self.session_active = False # Signal fired, reset session
                    return ctx.signal("long", confidence=0.8,
                                      metadata={"reason": "us_session_long_breakout",
                                                "session_open": self.session_open,
                                                "session_high": self.session_high,
                                                "current_price": current_price,
                                                "min_decisive_move": min_decisive_move,
                                                "ema_fast": ema_fast,
                                                "ema_slow": ema_slow,
                                                "rsi": rsi,
                                                "fear_greed": fear_greed})

                # Short Signal
                elif short_breakout and ema_fast < ema_slow and rsi > 30 and fear_greed < 70:
                    self.last_trade_bar_index = ctx.bar_index
                    self.session_active = False # Signal fired, reset session
                    return ctx.signal("short", confidence=0.8,
                                       metadata={"reason": "us_session_short_breakout",
                                                 "session_open": self.session_open,
                                                 "session_low": self.session_low,
                                                 "current_price": current_price,
                                                 "min_decisive_move": min_decisive_move,
                                                 "ema_fast": ema_fast,
                                                 "ema_slow": ema_slow,
                                                 "rsi": rsi,
                                                 "fear_greed": fear_greed})

        # End session at the end hour
        if current_hour == self.session_end_hour and self.session_active:
            self.session_active = False
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = 0.0
            self.session_volume = 0.0
            self.session_start_bar_index = -1

        if ctx.has_position():
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to implement cooldown after any position close
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass