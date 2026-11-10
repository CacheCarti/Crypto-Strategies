from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaDrift(Strategy):
    METADATA = {
        "name": "EthAsiaDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For median range calculation
        self.min_range_multiplier = 1.0 # Minimum range for a decisive move
        self.min_move_pct = 0.005 # 0.5% move as a threshold for decisiveness
        self.max_spike_pct = 0.008 # Max 0.8% for a single bar, to avoid spikes
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars

        # State variables for session logic
        self.asia_session_open_price = None
        self.asia_session_high = None
        self.asia_session_low = None
        self.asia_session_start_bar_index = None
        self.last_day_traded = -1 # To ensure only one entry per day

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 # Handle even/odd length

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        opens = ctx.opens(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- Session Tracking and Reset Logic ---
        # Asia Session: UTC 0 to 8 (exclusive for start, inclusive for end)
        # Reset at the start of a new Asia session (UTC 0)
        if current_hour == 0 and (self.asia_session_start_bar_index is None or ctx.bar_index > self.asia_session_start_bar_index + 8):
            self.asia_session_open_price = ctx.bar.open
            self.asia_session_high = ctx.bar.high
            self.asia_session_low = ctx.bar.low
            self.asia_session_start_bar_index = ctx.bar_index
            # Reset daily trade flag for the new day
            if current_day != self.last_day_traded:
                self.last_day_traded = -1 # Reset to allow trade for the new day

        # Update session high/low if within Asia session
        if self.asia_session_open_price is not None and 0 <= current_hour < 8:
            self.asia_session_high = max(self.asia_session_high, ctx.bar.high)
            self.asia_session_low = min(self.asia_session_low, ctx.bar.low)

        # --- Position Management ---
        # If we have a position, check exit conditions
        if ctx.has_position():
            # Exit before the next Asia session starts (UTC 8) if still open
            if current_hour == 7 and current_day != self.last_day_traded: # Exit at the end of the 7th hour, beginning of 8th
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Otherwise, hold the position
            return ctx.signal("hold")

        # --- Entry Logic ---
        # Only consider entries after cooldown and if not already traded today
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars or current_day == self.last_day_traded:
            return None

        # Calculate median range
        median_range = self._median_range(highs[:-1], lows[:-1], self.lookback_period) # Use previous bars for calculation
        if median_range is None:
            return None

        # Check for decisive Asia session move (transitioning out of Asia session into next)
        # Entry logic fires at UTC 8, after Asia session close
        if current_hour == 8 and self.asia_session_open_price is not None:
            session_close = closes[-1] # The close of the bar at UTC 7 (last bar of Asia session)
            session_high = self.asia_session_high
            session_low = self.asia_session_low

            # Calculate the overall move of the Asia session
            move_up = session_high - self.asia_session_open_price
            move_down = self.asia_session_open_price - session_low
            session_total_range = session_high - session_low

            # Check if the session move was decisive enough
            min_decisive_range = median_range * self.min_range_multiplier
            if session_total_range < min_decisive_range:
                return None # Not decisive enough

            # Check for single-bar spikes in the last bar of Asia session
            last_bar_range_pct = (highs[-1] - lows[-1]) / closes[-1]
            if last_bar_range_pct > self.max_spike_pct:
                return None # Avoid entering on a spike

            # Long entry condition: strong upward move in Asia session
            if move_up > move_down and move_up / self.asia_session_open_price > self.min_move_pct:
                self.last_signal_bar_index = ctx.bar_index
                self.last_day_traded = current_day
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_drift_long",
                        "session_open": self.asia_session_open_price,
                        "session_close_price": session_close,
                        "session_high": session_high,
                        "session_low": session_low,
                        "session_move_pct": move_up / self.asia_session_open_price,
                        "median_range": median_range,
                        "current_hour": current_hour,
                    }
                )

            # Short entry condition: strong downward move in Asia session
            elif move_down > move_up and move_down / self.asia_session_open_price > self.min_move_pct:
                self.last_signal_bar_index = ctx.bar_index
                self.last_day_traded = current_day
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_drift_short",
                        "session_open": self.asia_session_open_price,
                        "session_close_price": session_close,
                        "session_high": session_high,
                        "session_low": session_low,
                        "session_move_pct": move_down / self.asia_session_open_price,
                        "median_range": median_range,
                        "current_hour": current_hour,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive sizing or logging for closed positions
        pass