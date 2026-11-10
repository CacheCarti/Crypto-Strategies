from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningDrift(Strategy):
    METADATA = {
        "name": "EuropeanMorningDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 12   # UTC 12:00 (exclusive)
        self.exit_hour = 12        # UTC 12:00
        self.min_session_move_pct = 0.005 # 0.5% move
        self.lookback_period = 20  # For median range calculation
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for existing position
        if ctx.has_position():
            if current_hour == self.exit_hour and ctx.bar.timestamp.minute == 0:
                # Exit at the start of the exit hour
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour
                })
            return None # Hold position if not exit time

        # Entry logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        # Only consider entries during the specified European morning hours
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Check if this is the first bar of the entry window for the day
            # This prevents multiple signals on the same day's entry window
            if current_hour == self.entry_hour_start and ctx.bar.timestamp.minute != 0:
                return None

            # Get the session start price (e.g., open of the 7 UTC bar)
            # This requires looking back to the start of the current session window
            session_opens = []
            session_closes = []
            session_highs = []
            session_lows = []

            # Collect data from the start of the entry window up to the current bar
            # Assuming 1-hour bars, we need data for (current_hour - entry_hour_start + 1) bars
            bars_in_session = current_hour - self.entry_hour_start + 1
            
            if len(ctx.opens(bars_in_session)) < bars_in_session:
                return None # Not enough bars to form a session yet

            session_opens = ctx.opens(bars_in_session)
            session_closes = ctx.closes(bars_in_session)
            session_highs = ctx.highs(bars_in_session)
            session_lows = ctx.lows(bars_in_session)

            if not session_opens:
                return None

            session_start_price = session_opens[0]
            current_price = ctx.bar.close

            session_move_pct = (current_price - session_start_price) / session_start_price

            # Calculate median true range for volatility filter
            median_tr = self._median_range(highs, lows, self.lookback_period)
            if median_tr is None:
                return None

            # Determine minimum decisive move in absolute terms
            min_decisive_move_abs = session_start_price * self.min_session_move_pct

            # Condition 1: Decisive move in either direction
            is_decisive_long = session_move_pct > self.min_session_move_pct
            is_decisive_short = session_move_pct < -self.min_session_move_pct

            # Condition 2: Move exceeds median range (volatility filter)
            # And not a single bar spike (ensures move is sustained over multiple bars if applicable)
            # For 1-hour bars, ensure the current bar's move is not too extreme compared to the session move
            
            # Check if the current bar's move itself is not too large relative to the session move
            # This helps filter out single-bar spikes that might falsely trigger a "decisive" session move
            current_bar_move = abs(ctx.bar.close - ctx.bar.open)
            # If the session has only one bar, this condition is harder to apply,
            # so we focus on the overall session move being larger than median_tr
            
            # Ensure the total session move is greater than the median true range
            session_range = max(session_highs) - min(session_lows)
            
            move_exceeds_volatility = session_range > median_tr * 1.5 # Require a larger move than typical range

            # Don't take a signal if the last bar was an extreme spike (e.g., current bar's range is > 2x median_tr)
            is_spike = (ctx.bar.high - ctx.bar.low) > median_tr * 2.0

            if is_decisive_long and move_exceeds_volatility and not is_spike:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.65,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "europe_drift_long",
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "session_move_pct": session_move_pct,
                        "median_true_range": median_tr,
                        "bars_in_session": bars_in_session,
                        "entry_hour": current_hour
                    }
                )
            elif is_decisive_short and move_exceeds_volatility and not is_spike:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.65,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "europe_drift_short",
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "session_move_pct": session_move_pct,
                        "median_true_range": median_tr,
                        "bars_in_session": bars_in_session,
                        "entry_hour": current_hour
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # You can add adaptive logic here, e.g., adjust confidence or cooldown based on performance
        pass