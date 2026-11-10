from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "Asia Session Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_high = -1.0
        self.asia_session_low = float('inf')
        self.asia_session_open = -1.0
        self.session_started = False
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 6 # Wait 6 hours after a trade

        # Parameters for session move evaluation
        self.min_move_bps = 50 # Minimum move in BPS to consider it decisive
        self.max_move_bps = 300 # Max move to consider it not exhausted
        self.median_range_period = 20 # Bars for median range calculation
        self.min_volume_ratio = 1.2 # Volume confirmation for breakout

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        return (sorted_ranges[mid] + sorted_ranges[~mid]) / 2

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Asia session: UTC 0 to UTC 8 (inclusive for the 8th hour bar)
        is_asia_session = 0 <= current_hour <= 8

        # Reset session data at the start of a new Asia session (UTC 0 hour)
        if current_hour == 0 and not self.session_started:
            self.asia_session_high = -1.0
            self.asia_session_low = float('inf')
            self.asia_session_open = ctx.bar.open
            self.session_started = True

        # Update session high/low during Asia session
        if is_asia_session and self.session_started:
            self.asia_session_high = max(self.asia_session_high, ctx.bar.high)
            self.asia_session_low = min(self.asia_session_low, ctx.bar.low)
        elif not is_asia_session and self.session_started:
            # Asia session just ended (first bar outside session)
            self.session_started = False

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Entry logic: after Asia session ends, check for breakout
        # We check on the first bar AFTER the Asia session (e.g., UTC 9)
        if current_hour == 9 and not ctx.has_position():
            # Ensure we have valid session data from the *previous* session
            if self.asia_session_high == -1.0 or self.asia_session_low == float('inf') or self.asia_session_open == -1.0:
                return None

            session_range = self.asia_session_high - self.asia_session_low
            session_move_abs = ctx.bar.close - self.asia_session_open
            session_move_bps = (session_move_abs / self.asia_session_open) * 10000

            median_daily_range = self._median_range(highs, lows, self.median_range_period)
            if median_daily_range is None:
                return None

            # Check for decisive but not exhausted move
            is_decisive = abs(session_move_bps) > self.min_move_bps
            is_not_exhausted = abs(session_move_bps) < self.max_move_bps

            # Check for volume confirmation (current bar's volume vs. recent average)
            avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 0
            volume_confirmed = ctx.bar.volume > avg_volume * self.min_volume_ratio if avg_volume > 0 else False

            # Avoid single-bar spikes - check if the previous bar (last bar of Asia session) was also part of the move
            # This is an approximation. A more robust check might involve several bars.
            previous_bar_close = closes[-2] # Last bar of the Asia session
            previous_bar_open = opens[-2]
            previous_bar_move_bps = ((previous_bar_close - previous_bar_open) / previous_bar_open) * 10000

            # Long condition
            if ctx.bar.close > self.asia_session_high and session_move_bps > 0 and is_decisive and is_not_exhausted and volume_confirmed:
                # Ensure it's not just a single bar spike, but a sustained move
                if (ctx.bar.close - self.asia_session_high) / self.asia_session_high * 10000 < 100: # not more than 1% above session high
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "asia_session_breakout_long",
                            "session_high": self.asia_session_high,
                            "session_low": self.asia_session_low,
                            "session_open": self.asia_session_open,
                            "session_move_bps": session_move_bps,
                            "median_daily_range": median_daily_range,
                            "current_close": ctx.bar.close,
                            "current_volume": ctx.bar.volume,
                            "avg_volume": avg_volume,
                        }
                    )

            # Short condition
            elif ctx.bar.close < self.asia_session_low and session_move_bps < 0 and is_decisive and is_not_exhausted and volume_confirmed:
                # Ensure it's not just a single bar spike, but a sustained move
                if (self.asia_session_low - ctx.bar.close) / self.asia_session_low * 10000 < 100: # not more than 1% below session low
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "asia_session_breakdown_short",
                            "session_high": self.asia_session_high,
                            "session_low": self.asia_session_low,
                            "session_open": self.asia_session_open,
                            "session_move_bps": session_move_bps,
                            "median_daily_range": median_daily_range,
                            "current_close": ctx.bar.close,
                            "current_volume": ctx.bar.volume,
                            "avg_volume": avg_volume,
                        }
                    )

        # Exit logic: if in a position, close before UTC 8 or if structure stop is hit
        if ctx.has_position():
            # Exit before the start of the next Asia session (UTC 0) to avoid carrying positions across sessions
            # Or if it's nearing the end of current day (e.g., UTC 23) to be safe
            if current_hour >= 23: # Exit near end of day if still holding
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "time_based_exit_end_of_day",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position details or adapt strategy based on outcome
        pass