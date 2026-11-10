from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionDriftEth(Strategy):
    METADATA = {
        "name": "Session Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 sessions
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00 (9 AM ET)
        self.us_session_end_hour = 21   # UTC 21:00 (5 PM ET)
        self.lookback_period = 20       # For median range calculation
        self.min_session_move_factor = 1.5 # Session move must be 1.5x median range
        self.max_single_bar_spike_factor = 0.6 # Single bar should not be > 60% of total session move
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow trade immediately

        # Variables to track session state
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.session_direction = None # "long" or "short"

    def _median(self, values):
        s = sorted(values)
        mid = len(s) // 2
        return (s[mid-1] + s[mid]) / 2 if len(s) % 2 == 0 else s[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate median range for lookback_period
        if len(highs) > self.lookback_period and len(lows) > self.lookback_period:
            ranges = [highs[i] - lows[i] for i in range(1, self.lookback_period + 1)]
            median_range = self._median(ranges)
        else:
            median_range = 0 # Default if not enough data

        # Check for market regime for filtering
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Session Logic
        if current_hour == self.us_session_start_hour:
            # New US session starts
            if self.session_start_bar_index != ctx.bar_index: # Ensure this only triggers once per session start
                self.session_open_price = ctx.bar.open
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
                self.session_start_bar_index = ctx.bar_index
                self.session_direction = None # Reset session direction
        elif self.session_open_price is not None:
            # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic: Close position before US session end, or if regime changes unfavorably
        if ctx.has_position():
            if current_hour == self.us_session_end_hour and ctx.position_direction() is not None:
                # Ensure we close before the session ends
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Regime-based exit (optional, could be too aggressive)
            if ctx.position_direction() == "long" and trend_regime == "bear":
                 return ctx.signal("flat", confidence=0.5, metadata={"reason": "regime_change_exit_long", "trend_regime": trend_regime})
            if ctx.position_direction() == "short" and trend_regime == "bull":
                 return ctx.signal("flat", confidence=0.5, metadata={"reason": "regime_change_exit_short", "trend_regime": trend_regime})


        # Entry Logic: Only trade if not in a position and cooldown is over
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index > self.cooldown_bars):
            if self.session_open_price is not None and current_hour > self.us_session_start_hour and current_hour < self.us_session_end_hour:
                session_move = ctx.bar.close - self.session_open_price
                abs_session_move = abs(session_move)

                # Check if session move is decisive enough
                if median_range > 0 and abs_session_move > self.min_session_move_factor * median_range:
                    # Check for single-bar spike (avoid entering after a single large bar)
                    last_bar_move = abs(ctx.bar.close - ctx.bar.open)
                    if last_bar_move / abs_session_move < self.max_single_bar_spike_factor:

                        # Regime filter: only trade with the trend
                        if trend_regime == "bull" and session_move > 0 and regime != "CRISIS":
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.8,
                                metadata={
                                    "reason": "us_session_drift_long",
                                    "session_open_price": self.session_open_price,
                                    "current_price": ctx.bar.close,
                                    "session_move": session_move,
                                    "median_range": median_range,
                                    "trend_regime": trend_regime,
                                    "current_regime": regime,
                                }
                            )
                        elif trend_regime == "bear" and session_move < 0 and regime != "CRISIS":
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.8,
                                metadata={
                                    "reason": "us_session_drift_short",
                                    "session_open_price": self.session_open_price,
                                    "current_price": ctx.bar.close,
                                    "session_move": session_move,
                                    "median_range": median_range,
                                    "trend_regime": trend_regime,
                                    "current_regime": regime,
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session state on close if it makes sense, or log for adaptive sizing
        pass # No adaptive sizing for this strategy yet