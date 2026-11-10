from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-hour hold, but can be longer with session logic
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.cooldown_bars = 5       # Bars to wait after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate first trade

        # Parameters for range and exhaustion
        self.range_period = 20 # Bars for median range calculation
        self.min_move_ratio = 1.0 # Minimum move relative to median range
        self.max_exhaustion_ratio = 1.5 # Max move relative to median range to avoid exhaustion
        self.spike_threshold = 0.5 # Percentage of session move that can be a single bar spike

        # Store session data
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_price = None
        self.session_volume = 0.0
        self.session_bars_count = 0
        self.session_bar_index = -1

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid] + ranges[~mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new session (UTC 00:00)
        if current_hour == self.session_start_hour and ctx.bar_index != self.session_bar_index:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_price = ctx.bar.open
            self.session_volume = ctx.bar.volume
            self.session_bars_count = 1
            self.session_bar_index = ctx.bar_index
        elif current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            # Update session high/low within the session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_volume += ctx.bar.volume
            self.session_bars_count += 1

        # Check for trade entry AFTER the Asia session, if no position is open
        if not ctx.has_position() and current_hour == self.session_end_hour and \
           ctx.bar_index > self.last_trade_bar + self.cooldown_bars and \
           self.session_start_price is not None and self.session_bars_count > 1:

            session_range = self.session_high - self.session_low
            median_range = self._median_range(ctx.highs(self.range_period), ctx.lows(self.range_period), self.range_period)

            if median_range is None or median_range == 0:
                return None

            # Calculate session move
            session_close = ctx.bar.close # The close of the last bar of the session
            session_move = session_close - self.session_start_price

            # Check for decisive move
            is_decisive_long = session_move > 0 and session_move > median_range * self.min_move_ratio
            is_decisive_short = session_move < 0 and abs(session_move) > median_range * self.min_move_ratio

            # Check for exhaustion (move not too big)
            not_exhausted_long = session_move < median_range * self.max_exhaustion_ratio
            not_exhausted_short = abs(session_move) < median_range * self.max_exhaustion_ratio

            # Check for single bar spike (last bar's move vs total session move)
            last_bar_move = ctx.bar.close - ctx.bar.open
            not_spike_long = last_bar_move < session_move * self.spike_threshold
            not_spike_short = abs(last_bar_move) < abs(session_move) * self.spike_threshold


            if is_decisive_long and not_exhausted_long and not_spike_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "asia_session_breakout_long",
                        "session_start_price": self.session_start_price,
                        "session_end_price": session_close,
                        "session_move": session_move,
                        "median_range": median_range,
                        "current_price": ctx.bar.close,
                    }
                )
            elif is_decisive_short and not_exhausted_short and not_spike_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "asia_session_breakout_short",
                        "session_start_price": self.session_start_price,
                        "session_end_price": session_close,
                        "session_move": session_move,
                        "median_range": median_range,
                        "current_price": ctx.bar.close,
                    }
                )
        
        # Exit logic: if we are in a position and it's approaching the next Asia session, consider flattening
        # This prevents holding trades across the next session's open, aligning with the strategy's session focus
        if ctx.has_position() and current_hour == self.session_start_hour - 1: # One hour before next session
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "pre_next_asia_session_exit",
                    "current_price": ctx.bar.close,
                    "position_direction": ctx.position_direction(),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close details or adjust parameters
        pass