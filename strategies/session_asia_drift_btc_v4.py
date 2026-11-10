from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SessionAsiaDriftBTC(Strategy):
    METADATA = {
        "name": "SessionAsiaDriftBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range calculation
        self.min_range_factor = 1.0 # Minimum range for a decisive move, relative to median
        self.max_range_factor = 3.0 # Maximum range to avoid exhausted moves
        self.min_move_pct = 0.001 # Minimum percentage move to consider it significant
        self.max_move_pct = 0.01 # Maximum percentage move to avoid exhaustion
        self.last_trade_bar_index = -1
        self.cooldown_bars = 4 # Cooldown period after a trade
        self.session_end_hour = 8 # UTC hour for Asia session close
        self.entry_hour_start = 8 # UTC hour to look for entry after Asia session
        self.entry_hour_end = 12 # UTC hour to stop looking for entry

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.lookback_period + 1 or len(highs) < self.lookback_period or len(lows) < self.lookback_period:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None
        
        # If we have an open position, manage it
        if ctx.has_position():
            # If current hour is near session end, exit to prevent holding too long
            # Or if it's past our typical hold time
            if current_hour == self.session_end_hour or current_hour >= self.entry_hour_end + 2:
                # Exit with a lower confidence as it's a time-based exit
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_based_exit_session_end",
                    "current_hour": current_hour,
                    "session_end_hour": self.session_end_hour
                })
            return None # Hold position

        # Only look for entry signals after the Asia session closes, within a window
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Calculate Asia session range (UTC 0-8)
        # We need to find the bars within the last Asia session (previous day's 0-8 UTC)
        # This requires looking back up to 8 bars if current_hour is 8.
        
        # Find the start of the current day's Asia session (UTC 0)
        current_day_start_bar_idx = ctx.bar_index - current_hour
        
        # Find the start of the previous day's Asia session (UTC 0)
        prev_day_asia_start_bar_idx = current_day_start_bar_idx - 24 
        prev_day_asia_end_bar_idx = prev_day_asia_start_bar_idx + self.session_end_hour

        # Ensure we have enough history for the previous Asia session
        if prev_day_asia_start_bar_idx < 0:
            return None

        asia_session_highs = []
        asia_session_lows = []
        asia_session_opens = []
        asia_session_closes = []

        # Collect OHLC for the previous Asia session
        for i in range(prev_day_asia_start_bar_idx, prev_day_asia_end_bar_idx):
            if i >= 0 and i < ctx.bar_index: # Ensure indices are valid
                asia_session_highs.append(ctx.highs(ctx.bar_index - i)[0])
                asia_session_lows.append(ctx.lows(ctx.bar_index - i)[0])
                asia_session_opens.append(ctx.opens(ctx.bar_index - i)[0])
                asia_session_closes.append(ctx.closes(ctx.bar_index - i)[0])

        if not asia_session_highs or not asia_session_lows:
            return None
        
        # Calculate the range and direction of the Asia session
        asia_session_max_high = max(asia_session_highs)
        asia_session_min_low = min(asia_session_lows)
        asia_session_open = asia_session_opens[0]
        asia_session_close = asia_session_closes[-1]

        asia_session_range = asia_session_max_high - asia_session_min_low
        asia_session_move = asia_session_close - asia_session_open
        
        if asia_session_range == 0: # Avoid division by zero
            return None

        # Calculate median true range (ATR-like) for the lookback period
        true_ranges = []
        for i in range(1, self.lookback_period):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            true_ranges.append(tr)
        
        if not true_ranges:
            return None
        
        median_true_range = statistics.median(true_ranges)
        
        # Check if the Asia session move is decisive but not exhausted
        if asia_session_range < median_true_range * self.min_range_factor:
            return None # Not decisive enough
        
        if asia_session_range > median_true_range * self.max_range_factor:
            return None # Too exhausted
        
        # Check if the move is significant in percentage terms
        move_pct = abs(asia_session_move / asia_session_open)
        if move_pct < self.min_move_pct or move_pct > self.max_move_pct:
            return None # Filter out small or too large moves

        # Regime filter: Only trade in 'normal' or 'volatile' regimes
        current_regime = ctx.market.get("regime", "NORMAL")
        if current_regime not in ["NORMAL", "HIGH_VOL"]:
            return None

        # Determine signal based on Asia session direction
        signal_direction = None
        confidence = 0.0

        if asia_session_move > 0: # Bullish Asia session
            signal_direction = "long"
            confidence = 0.7
        elif asia_session_move < 0: # Bearish Asia session
            signal_direction = "short"
            confidence = 0.7

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": f"asia_session_drift_{signal_direction}",
                    "asia_session_move_pct": f"{asia_session_move / asia_session_open:.4f}",
                    "asia_session_range_vs_median_tr": f"{asia_session_range / median_true_range:.2f}",
                    "current_regime": current_regime,
                    "entry_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive sizing or logging based on position outcome
        pass