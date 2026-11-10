from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionDriftBTC(Strategy):
    METADATA = {
        "name": "SessionDriftBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session start)
        self.session_end_hour = 24   # UTC 23:59 (end of session)
        self.exit_hour = 0           # UTC 0:00 (exit before new day)
        self.min_session_move_pct = 0.25 # Minimum move percentage for a "decisive" session
        self.median_range_period = 20 # Period for median range calculation
        self.cooldown_bars = 6       # Cooldown after a trade (6 hours)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        return (ranges[mid - 1] + ranges[mid]) / 2 if len(ranges) % 2 == 0 else ranges[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold")
            else:
                return None # Still in cooldown, do not open new position

        # Exit existing position logic
        if ctx.has_position():
            # Exit if it's the exit hour (UTC 0) or if regime changes
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # Regime-gated exit: if market becomes volatile or crisis, exit.
            # This is an additional safety net.
            if ctx.market.get("regime", "NORMAL") in ["HIGH_VOL", "CRISIS"]:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "regime_change_exit", "current_regime": ctx.market.get("regime")})

            return ctx.signal("hold")

        # Entry logic: Only consider entries during the session_start_hour
        if current_hour == self.session_start_hour:
            # Check if this is the first bar of the session start hour
            # To avoid re-evaluating the same session multiple times if bars are
            # for example 5 minutes within the same hour
            if ctx.bar.timestamp.minute != 0:
                return None

            # Gather session data from the previous 6 bars (18:00 to 23:00 UTC)
            session_closes = ctx.closes(self.session_end_hour - self.session_start_hour)
            session_opens = ctx.opens(self.session_end_hour - self.session_start_hour)
            session_highs = ctx.highs(self.session_end_hour - self.session_start_hour)
            session_lows = ctx.lows(self.session_end_hour - self.session_start_hour)

            if len(session_closes) < (self.session_end_hour - self.session_start_hour):
                return None # Not enough data for the full session

            session_open_price = session_opens[0]
            session_close_price = session_closes[-1]
            session_min_price = min(session_lows)
            session_max_price = max(session_highs)

            # Calculate session move
            session_move_abs = session_close_price - session_open_price
            session_move_pct = (session_move_abs / session_open_price) * 100

            # Calculate median true range for comparison
            median_tr = self._median_range(ctx.highs(self.median_range_period), ctx.lows(self.median_range_period), self.median_range_period)
            if median_tr is None:
                return None

            # Determine if the session move is decisive
            decisive_move_threshold = (median_tr / session_open_price) * 100 * 0.75 # A bit less than median range, but still significant
            
            if abs(session_move_pct) < self.min_session_move_pct or abs(session_move_pct) < decisive_move_threshold:
                return None # Not decisive enough

            # Avoid single-bar spikes - check if the move was a single large bar
            # This is a simplification; a more robust check would involve comparing
            # movement across multiple bars. For now, check if the session close is
            # significantly different from session high/low.
            # If the session closed very near its high or low, it might be a spike.
            # A more robust approach might be to check the range of the last bar vs. overall session range.
            # For simplicity, we'll check if the session move is disproportionately large compared to its range.
            session_range = session_max_price - session_min_price
            if session_range == 0: # Avoid division by zero
                return None

            # If the session move is a very large portion of the total session range, it might be a spike.
            # E.g., if move is 90% of range, it's very directional, maybe too much.
            # This is a heuristic.
            if abs(session_move_abs) / session_range > 0.85: # If move covers > 85% of session range
                 return None # Likely a single-bar spike or too exhausted

            # Regime filter: only trade in NORMAL or CALM regimes
            current_regime = ctx.market.get("regime", "NORMAL")
            if current_regime not in ["NORMAL", "CALM"]:
                return None # Stand down in volatile/crisis regimes

            # Trend filter: only trade with the trend
            trend_regime = ctx.market.get("trend_regime", "neutral")
            
            signal_direction = None
            if session_move_pct > 0 and trend_regime in ["bull", "neutral"]: # Bullish session, follow long
                signal_direction = "long"
            elif session_move_pct < 0 and trend_regime in ["bear", "neutral"]: # Bearish session, follow short
                signal_direction = "short"
            else:
                return None # Session move is against the trend or trend is too neutral for this move

            if signal_direction:
                confidence = min(0.5 + (abs(session_move_pct) / (median_tr / session_open_price * 100)) * 0.2, 0.9) # Scale confidence by decisiveness
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": f"session_drift_{signal_direction}",
                        "session_move_pct": session_move_pct,
                        "median_tr_pct": (median_tr / session_open_price) * 100,
                        "current_regime": current_regime,
                        "trend_regime": trend_regime,
                        "session_open": session_open_price,
                        "session_close": session_close_price,
                        "session_range_ratio": abs(session_move_abs) / session_range
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Reset cooldown or adapt logic based on trade outcome
        # For this strategy, cooldown is managed in on_bar.
        pass