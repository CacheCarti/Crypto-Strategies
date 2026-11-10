from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionImpulseCarry(Strategy):
    METADATA = {
        "name": "Session Impulse Carry BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours max hold
        "warmup_bars": 30,
        "required_features": [], # No specific features used beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length = 6 # hours for impulse measurement (18-24 UTC)
        self.entry_hour_start = 18 # UTC
        self.entry_hour_end = 23 # UTC (allows entry up to 23:59:59)
        self.exit_hour = 0 # UTC, exit before this hour
        self.min_impulse_std = 0.5 # Minimum standard deviation of returns for a valid impulse
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + self.session_length)
        if len(closes) < self.warmup_bars + self.session_length:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Position management: exit condition
        if ctx.has_position():
            # Exit before the exit_hour
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_boundary_exit", "current_hour": current_hour})
            return None # Hold position otherwise

        # Entry logic: only during the specified entry window
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Check if we have enough bars to measure the session's impulse
            # We need bars from (current_hour - session_length) up to current_hour
            required_prev_bars = self.session_length
            if len(closes) < required_prev_bars + 1: # +1 for current bar
                return None

            # Calculate returns for the session window
            session_closes = closes[-(required_prev_bars + 1):] # Include current bar close for return calc
            session_returns = [(session_closes[i] - session_closes[i-1]) / session_closes[i-1] for i in range(1, len(session_closes))]

            if not session_returns:
                return None

            avg_return = sum(session_returns) / len(session_returns)
            std_dev_returns = self._stddev(session_returns, len(session_returns))

            if std_dev_returns is None or std_dev_returns < 0.0001: # Avoid division by zero and tiny std dev
                return None

            # Z-score of the average return to gauge impulse strength
            # Z-score measures how many standard deviations the average return is from zero
            z_score_impulse = avg_return / std_dev_returns if std_dev_returns > 0 else 0.0

            # Only consider impulses with a certain minimum volatility/deviation
            if std_dev_returns < self.min_impulse_std / 100: # Convert bps to decimal
                return None

            # Determine direction based on the last bar's move and the overall session impulse
            last_bar_return = (ctx.bar.close - ctx.bar.open) / ctx.bar.open
            
            signal = None
            reason = ""
            confidence = 0.0

            # Long entry: Positive impulse and last bar confirms
            if z_score_impulse > 0.5 and last_bar_return > 0:
                signal = "long"
                reason = "positive_session_impulse_carry"
                confidence = min(0.5 + abs(z_score_impulse) * 0.1, 0.9) # Scale confidence by impulse strength
            # Short entry: Negative impulse and last bar confirms
            elif z_score_impulse < -0.5 and last_bar_return < 0:
                signal = "short"
                reason = "negative_session_impulse_carry"
                confidence = min(0.5 + abs(z_score_impulse) * 0.1, 0.9)

            if signal:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "session_avg_return": avg_return * 100,
                        "session_std_dev_returns": std_dev_returns * 100,
                        "z_score_impulse": z_score_impulse,
                        "last_bar_return": last_bar_return * 100,
                        "close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this strategy, just logging
        pass