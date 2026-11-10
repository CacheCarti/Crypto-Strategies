from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaDriftSOL(Strategy):
    METADATA = {
        "name": "Session Asia Drift SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended 2-hour hold, but can be longer if session drift continues
        "warmup_bars": 100, # Need enough bars for median range and volatility
        "required_features": [], # No external features needed for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 50 # For median range calculation
        self.min_session_move_bps = 50 # Minimum move in BPS to consider "decisive"
        self.max_session_move_bps = 300 # Max move to avoid "exhausted" moves
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours for 1h bars)
        self.last_trade_bar = -self.cooldown_bars
        self.last_asia_close_price = None
        self.trade_today = False # Flag to ensure only one trade per day
        self.entry_hour = 8 # Enter after Asia session close (UTC 8)

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
        closes = ctx.closes(self.lookback_period)
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset trade_today flag at the start of a new day (UTC 0 hour)
        if current_hour == 0 and not self.trade_today:
            self.trade_today = False # Ensure it's reset only once per day
        if current_hour == 0 and self.last_asia_close_price is not None:
             # Reset last_asia_close_price at the start of a new trading day (UTC 0)
             # to avoid carrying over old session data.
            self.last_asia_close_price = None
            self.trade_today = False


        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic for exiting existing positions
        if ctx.has_position():
            # Exit before UTC 8 if still holding
            if current_hour == 7 and ctx.position_direction() is not None: # Exit at 7:59 UTC
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour})

            # Structure stop: if price goes against us significantly
            # Example: if current price is below entry for a long, or above for a short, by a certain percentage
            if ctx.position["direction"] == "long":
                if ctx.bar.close < ctx.position["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000 * 0.75): # Proactive stop at 75% of SL
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "structure_stop_long", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
            elif ctx.position["direction"] == "short":
                if ctx.bar.close > ctx.position["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000 * 0.75): # Proactive stop at 75% of SL
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "structure_stop_short", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
            return None # Hold position if no exit condition met

        # Logic for new entries (only if no position and no trade today)
        if not ctx.has_position() and not self.trade_today:
            # Capture Asia session close price (UTC 8)
            if current_hour == 7: # This is the last bar of the Asia session (0-7 UTC)
                self.last_asia_close_price = ctx.bar.close
                return None

            # Entry logic: after Asia session close (UTC 8 and later)
            if current_hour == self.entry_hour and self.last_asia_close_price is not None:
                asia_open_price = ctx.opens(self.entry_hour + 1)[0] # Open of the Asia session (UTC 0)
                asia_close_price = self.last_asia_close_price

                if asia_close_price is None: # Should not happen if current_hour == 8
                    return None

                session_move = (asia_close_price - asia_open_price) / asia_open_price * 10000 # in bps

                median_range_value = self._median_range(highs, lows, self.lookback_period)
                if median_range_value is None:
                    return None
                
                # Convert median_range_value to bps relative to current price for comparison
                median_range_bps = (median_range_value / ctx.bar.close) * 10000

                # Determine if the session move is decisive but not exhausted
                is_decisive = abs(session_move) >= self.min_session_move_bps
                is_not_exhausted = abs(session_move) <= self.max_session_move_bps

                # Avoid single-bar spikes: check if the move was sustained
                # Simple check: if the last bar's body was a significant portion of the session move
                if len(closes) < 2: return None
                last_bar_move_bps = (closes[-1] - closes[-2]) / closes[-2] * 10000
                if abs(last_bar_move_bps) > abs(session_move) * 0.75: # If last bar accounts for > 75% of the move
                    return None # Likely a spike, not a sustained drift

                # Ensure the move exceeds a recent median range
                exceeds_median_range = abs(session_move) > median_range_bps * 0.5 # require move to be at least 50% of median bar range

                if is_decisive and is_not_exhausted and exceeds_median_range:
                    if session_move > 0: # Asia session was bullish, follow long
                        self.last_trade_bar = ctx.bar_index
                        self.trade_today = True
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_drift_long",
                                "session_move_bps": session_move,
                                "median_range_bps": median_range_bps,
                                "asia_open": asia_open_price,
                                "asia_close": asia_close_price,
                                "entry_bar_close": ctx.bar.close
                            }
                        )
                    elif session_move < 0: # Asia session was bearish, follow short
                        self.last_trade_bar = ctx.bar_index
                        self.trade_today = True
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_drift_short",
                                "session_move_bps": session_move,
                                "median_range_bps": median_range_bps,
                                "asia_open": asia_open_price,
                                "asia_close": asia_close_price,
                                "entry_bar_close": ctx.bar.close
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass