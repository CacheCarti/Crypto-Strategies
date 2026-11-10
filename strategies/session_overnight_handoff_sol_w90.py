from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightImpulseSOL(Strategy):
    METADATA = {
        "name": "SOL Overnight Impulse with Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours max hold
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # For EMA and volatility
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.entry_cooldown_bars
        self.overnight_start_hour = 0 # UTC 00:00
        self.overnight_end_hour = 6 # UTC 06:00
        self.exit_hour = 12 # Exit before US session (UTC 12:00)
        self.min_impulse_std_dev = 0.5 # Minimum impulse magnitude in std dev

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic
        if ctx.has_position():
            # Exit if past the exit hour, irrespective of profit/loss
            if current_hour == self.exit_hour and ctx.bar_index > self.last_trade_bar:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_handoff_exit",
                        "current_hour": current_hour,
                        "entry_bar": ctx.position.get("entry_bar_index"),
                        "current_price": ctx.bar.close,
                    }
                )
            return None # Hold position if not exit time

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.entry_cooldown_bars:
            return None

        # Entry logic - only during overnight session
        if current_hour >= self.overnight_start_hour and current_hour < self.overnight_end_hour:
            # We need at least 'lookback_period' bars for EMA and StdDev
            if len(closes) < self.lookback_period:
                return None

            # Calculate overnight session's impulse
            # We need opens and closes for the current session up to this point
            session_closes = []
            session_opens = []
            session_highs = []
            session_lows = []
            session_volumes = []

            # Collect data for the current session
            for i in range(len(ctx.closes(ctx.bar_index + 1))): # Iterate all available bars
                bar_hour = ctx.bar.timestamp.hour - (len(ctx.closes(ctx.bar_index + 1)) - 1 - i) % 24 # Crude hour calculation for past bars
                if bar_hour >= self.overnight_start_hour and bar_hour < self.overnight_end_hour:
                    session_closes.append(ctx.closes(ctx.bar_index + 1)[i])
                    session_opens.append(ctx.opens(ctx.bar_index + 1)[i])
                    session_highs.append(ctx.highs(ctx.bar_index + 1)[i])
                    session_lows.append(ctx.lows(ctx.bar_index + 1)[i])
                    session_volumes.append(ctx.volumes(ctx.bar_index + 1)[i])

            if len(session_closes) < 2: # Need at least two bars to determine impulse
                return None

            # Impulse calculation: difference from session open to current close
            # We use the first open of the session for the anchor
            session_start_price = session_opens[0]
            current_session_price = ctx.bar.close
            
            # Calculate volatility based on recent price action (e.g., ATR or stddev of closes)
            recent_closes = ctx.closes(self.lookback_period)
            if len(recent_closes) < self.lookback_period:
                return None
            
            volatility = self._stddev(recent_closes, self.lookback_period)
            if volatility is None or volatility == 0:
                return None

            impulse = (current_session_price - session_start_price)
            impulse_normalized = impulse / volatility

            # Entry conditions: significant impulse in one direction
            # and only at the end of the overnight session (e.g., last hour)
            if current_hour == self.overnight_end_hour - 1: # Trigger in the last hour of overnight
                if impulse_normalized > self.min_impulse_std_dev: # Strong bullish impulse
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=min(0.7 + impulse_normalized * 0.1, 0.9), # Scale confidence by impulse strength
                        metadata={
                            "reason": "overnight_bullish_impulse",
                            "impulse_normalized": impulse_normalized,
                            "session_start_price": session_start_price,
                            "current_price": current_session_price,
                            "volatility": volatility,
                            "current_hour": current_hour,
                        }
                    )
                elif impulse_normalized < -self.min_impulse_std_dev: # Strong bearish impulse
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=min(0.7 + abs(impulse_normalized) * 0.1, 0.9), # Scale confidence by impulse strength
                        metadata={
                            "reason": "overnight_bearish_impulse",
                            "impulse_normalized": impulse_normalized,
                            "session_start_price": session_start_price,
                            "current_price": current_session_price,
                            "volatility": volatility,
                            "current_hour": current_hour,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to current_bar_index to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # Optional: Log or adapt strategy based on position outcome
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars.")