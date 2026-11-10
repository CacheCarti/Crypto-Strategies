from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaSessionFade(Strategy):
    METADATA = {
        "name": "BtcAsiaSessionFade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-4 hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 0
        self.session_end_hour = 8    # UTC 8 (exclusive)
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_close_price = None
        self.session_bar_count = 0
        self.traded_this_session = False
        self.session_bars = [] # Store prices for session average
        self.max_session_move = 0.0 # Track max move in bps from session open

        self.ema_period_slow = 20 # For trend filtering
        self.rsi_period = 14 # For overbought/oversold filtering
        self.min_stretch_bps = 50.0 # Min session move in bps to consider fading
        self.max_session_bars_for_entry = 5 # Only enter up to this bar within the session

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
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
        
        # Calculate initial averages
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_session_start = (current_hour == self.session_start_hour) and (self.session_bar_count == 0)
        is_session_end_bar = (current_hour == (self.session_end_hour - 1)) # Last bar of the session

        closes = ctx.closes(self.ema_period_slow + 1)
        if len(closes) < self.ema_period_slow + 1:
            return None
        
        current_price = ctx.bar.close
        slow_ema = self._ema(closes, self.ema_period_slow)
        
        if slow_ema is None:
            return None

        # --- Session Tracking ---
        if is_session_start:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open_price = ctx.bar.open
            self.session_close_price = None # Reset for the new session
            self.session_bar_count = 0
            self.traded_this_session = False
            self.session_bars = []
            self.max_session_move = 0.0
            # If we had a position open from previous session, and it's still open, exit it.
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit"})

        # Update session stats if within session hours
        if self.session_start_hour <= current_hour < self.session_end_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bar_count += 1
            self.session_bars.append(ctx.bar.close)
            if self.session_open_price:
                current_move_from_open = (current_price - self.session_open_price) / self.session_open_price * 10000
                self.max_session_move = max(self.max_session_move, abs(current_move_from_open))

        # --- Position Management (Exit at session end) ---
        if ctx.has_position() and is_session_end_bar:
            # Always exit before session end for a fresh start
            return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # --- Entry Logic ---
        if ctx.has_position() or self.traded_this_session:
            return None # Already in a position or traded this session

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None # Cooldown active

        # Only consider entries up to a certain point in the session
        if not (self.session_start_hour <= current_hour < self.session_end_hour) or \
           self.session_bar_count > self.max_session_bars_for_entry or \
           self.session_bar_count == 0: # Ensure session data is initialized
            return None

        # Regime and Trend Filters
        # Do not fade strong trends (price well above/below slow EMA)
        # Do not fade during crisis regimes
        if ctx.market.get("regime", "NORMAL") == "CRISIS" or \
           ctx.market.get("regime", "NORMAL") == "MELTDOWN":
            return None

        # Avoid fading when the price is strongly trending above/below the slow EMA
        # Define "strong trend" as being outside a certain band around the EMA
        ema_band_bps = 25.0 # e.g., 25 bps away from EMA
        if current_price > slow_ema * (1 + ema_band_bps / 10000) or \
           current_price < slow_ema * (1 - ema_band_bps / 10000):
            return None
        
        # Also filter by BTC return pct - avoid fading if BTC is having a very strong day
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        if abs(btc_return_pct) > 1.5: # If BTC moved more than 1.5% in 24h, probably a strong trend
            return None

        # Calculate session midpoint and current stretch
        if self.session_bar_count > 1 and self.session_open_price:
            session_midpoint = (self.session_high + self.session_low) / 2
            
            # Stretch from session open
            stretch_from_open_bps = (current_price - self.session_open_price) / self.session_open_price * 10000

            # Only consider fading if the session has moved significantly
            if self.max_session_move < self.min_stretch_bps:
                return None

            # Fading Logic: Price has moved significantly and is now stalling/reversing
            # Long entry: price is significantly below session open, but has moved up from session low
            # Short entry: price is significantly above session open, but has moved down from session high

            current_rsi = self._rsi(ctx.closes(self.rsi_period + 1), self.rsi_period)
            if current_rsi is None:
                return None

            signal_confidence = 0.6 # Base confidence

            # Short signal: Price stretched significantly up, now pulling back towards midpoint
            if stretch_from_open_bps > self.min_stretch_bps and \
               current_price > session_midpoint and \
               ctx.bar.close < ctx.bar.open and \
               current_rsi > 60: # Overbought condition
                
                # Further refine: check if current price is closer to session high than open (still stretched)
                # And if it's starting to turn down after hitting session high
                if current_price > self.session_open_price and ctx.bar.close < self.session_high:
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "asia_session_fade_short",
                            "session_open": self.session_open_price,
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_midpoint": session_midpoint,
                            "stretch_bps": stretch_from_open_bps,
                            "current_rsi": current_rsi,
                            "current_price": current_price,
                            "slow_ema": slow_ema,
                            "bar_index": ctx.bar_index,
                        }
                    )

            # Long signal: Price stretched significantly down, now pulling back towards midpoint
            elif stretch_from_open_bps < -self.min_stretch_bps and \
                 current_price < session_midpoint and \
                 ctx.bar.close > ctx.bar.open and \
                 current_rsi < 40: # Oversold condition
                
                # Further refine: check if current price is closer to session low than open (still stretched)
                # And if it's starting to turn up after hitting session low
                if current_price < self.session_open_price and ctx.bar.close > self.session_low:
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "asia_session_fade_long",
                            "session_open": self.session_open_price,
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_midpoint": session_midpoint,
                            "stretch_bps": stretch_from_open_bps,
                            "current_rsi": current_rsi,
                            "current_price": current_price,
                            "slow_ema": slow_ema,
                            "bar_index": ctx.bar_index,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session if the position closed for any reason
        # This allows for potentially re-entering within the same session if conditions re-align
        # However, the `self.traded_this_session` flag will still prevent multiple entries
        # within the same bar, and the cooldown will prevent rapid re-entry.
        pass