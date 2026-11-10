from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionHandoffBTC(Strategy):
    METADATA = {
        "name": "SessionHandoffBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour, aiming for a single session handoff
        "warmup_bars": 50, # Need enough bars for 20-period EMA
        "required_features": [], # No external features needed for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.min_session_impulse_candles = 3 # Minimum candles to establish impulse
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the first 'period' values
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Logic for exiting an existing position
        if ctx.has_position():
            # If current hour is 21 UTC or later, exit to avoid holding past session boundary
            if current_hour >= 21:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_boundary_exit", "current_hour": current_hour})
            return None # Hold position if not time to exit

        # Entry logic: US session (13-21 UTC)
        # Look for impulse during the US session to trade the handoff
        if current_hour == 18: # 5 hours into US session (13-18 UTC impulse window)
            # Ensure we have enough bars for comparison within the session
            session_opens = ctx.opens(self.min_session_impulse_candles + 1)
            session_closes = ctx.closes(self.min_session_impulse_candles + 1)
            if len(session_opens) < self.min_session_impulse_candles + 1:
                return None

            # Calculate price change over the last 'min_session_impulse_candles' bars
            price_change = closes[-1] - closes[-self.min_session_impulse_candles]
            
            # Use EMA for trend confirmation
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            confidence = 0.0
            signal_direction = None
            reason = ""

            # Long entry condition
            if price_change > 0 and closes[-1] > ema:
                confidence = 0.65
                signal_direction = "long"
                reason = "us_session_bullish_impulse_ema_confirm"
            # Short entry condition
            elif price_change < 0 and closes[-1] < ema:
                confidence = 0.65
                signal_direction = "short"
                reason = "us_session_bearish_impulse_ema_confirm"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "price_change_last_n_bars": price_change,
                        "ema_period": self.ema_period,
                        "current_close": closes[-1],
                        "current_ema": ema,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, reset any adaptive state if necessary
        # For this strategy, the cooldown is handled in on_bar, no need to reset here.
        pass