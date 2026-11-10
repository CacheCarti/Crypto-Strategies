from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionGapReversion(Strategy):
    METADATA = {
        "name": "Sol Session Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-hour bar, so 1-6 hours hold
        "warmup_bars": 24, # Need enough bars to define prior session
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Cooldown on startup
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade

        # Store prior session's close to detect gaps
        self.prior_session_close = None
        self.prior_session_end_bar_index = -1
        self.prior_session_data = []

        # Indicators for confirmation/filtering
        self.fast_ema_period = 5
        self.slow_ema_period = 13
        self.rsi_period = 7 # Faster RSI for quicker gap rejections

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # --- Session Management ---
        # Late US session: UTC 18-24 (inclusive of 18, exclusive of 0 next day)
        # We define a "session end" at 23:00 UTC (i.e., the bar for 23:00 to 00:00)
        # and "session start" at 18:00 UTC.

        # If it's the end of the US session (23:00 UTC bar), record its close
        if current_hour == 23 and ctx.bar_index > self.prior_session_end_bar_index:
            self.prior_session_close = current_close
            self.prior_session_end_bar_index = ctx.bar_index
            self.prior_session_data = [] # Reset for the new session
            return None # Don't trade on the session-defining bar

        # Collect data for the current session if within the window
        if 18 <= current_hour < 23:
            self.prior_session_data.append(current_close)
        
        # --- Trading Logic ---
        # Only consider trades at the start of the late US session (18:00 - 22:00 UTC)
        # And only if a prior session close was recorded and enough bars have passed
        if not (18 <= current_hour < 23) or self.prior_session_close is None:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Define the "open" of the current session as the close of the 18:00 bar
        # We need at least one bar into the session to define the "open"
        if len(self.prior_session_data) < 1:
            return None # Not enough data for current session open

        current_session_open = self.prior_session_data[0] # Close of the 18:00 bar

        gap_size_bps = ((current_session_open - self.prior_session_close) / self.prior_session_close) * 10000
        
        # Gap threshold - consider a gap significant if > 50 bps
        significant_gap_bps = 50.0
        
        # Gap rejection threshold - how much has price moved against the gap since session open
        gap_rejection_bps = 25.0

        # Long Entry: Down Gap Reversion
        if gap_size_bps < -significant_gap_bps: # Significant down gap
            # Price has started to move back up from the open, rejecting the gap
            if current_close > current_session_open and \
               (current_close - current_session_open) / current_session_open * 10000 > gap_rejection_bps:
                # Confirm with bullish momentum (fast EMA above slow EMA) and not overbought RSI
                if fast_ema > slow_ema and rsi < 70:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "late_us_down_gap_rejection_long",
                            "prior_session_close": self.prior_session_close,
                            "current_session_open": current_session_open,
                            "gap_size_bps": round(gap_size_bps, 2),
                            "current_close": current_close,
                            "fast_ema": round(fast_ema, 2),
                            "slow_ema": round(slow_ema, 2),
                            "rsi": round(rsi, 2),
                            "regime": ctx.regime,
                        }
                    )

        # Short Entry: Up Gap Reversion
        elif gap_size_bps > significant_gap_bps: # Significant up gap
            # Price has started to move back down from the open, rejecting the gap
            if current_close < current_session_open and \
               (current_session_open - current_close) / current_session_open * 10000 > gap_rejection_bps:
                # Confirm with bearish momentum (fast EMA below slow EMA) and not oversold RSI
                if fast_ema < slow_ema and rsi > 30:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "late_us_up_gap_rejection_short",
                            "prior_session_close": self.prior_session_close,
                            "current_session_open": current_session_open,
                            "gap_size_bps": round(gap_size_bps, 2),
                            "current_close": current_close,
                            "fast_ema": round(fast_ema, 2),
                            "slow_ema": round(slow_ema, 2),
                            "rsi": round(rsi, 2),
                            "regime": ctx.regime,
                        }
                    )

        # --- Exit Logic (if holding a position) ---
        if ctx.has_position():
            # Exit before UTC 0 (i.e., on the 23:00 UTC bar)
            if current_hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.9, # High confidence for time-based exit
                    metadata={
                        "reason": "time_based_session_exit",
                        "current_hour": current_hour,
                        "current_close": current_close,
                        "entry_price": ctx.position["entry_price"],
                    }
                )
            
            # Additional exit condition: if price reverts past the prior session close
            # This is a "structure stop" - if the gap has been fully filled AND momentum turns against us
            if ctx.position_direction() == "long":
                if current_close > self.prior_session_close and fast_ema < slow_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_gap_filled_momentum_reverse",
                            "prior_session_close": self.prior_session_close,
                            "current_close": current_close,
                            "fast_ema": round(fast_ema, 2),
                            "slow_ema": round(slow_ema, 2),
                        }
                    )
            elif ctx.position_direction() == "short":
                if current_close < self.prior_session_close and fast_ema > slow_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_gap_filled_momentum_reverse",
                            "prior_session_close": self.prior_session_close,
                            "current_close": current_close,
                            "fast_ema": round(fast_ema, 2),
                            "slow_ema": round(slow_ema, 2),
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic based on position close for this strategy
        pass