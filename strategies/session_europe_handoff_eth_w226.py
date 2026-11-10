from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningImpulse(Strategy):
    METADATA = {
        "name": "European Morning Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # ~1 hour, but can be longer if session extends
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 12   # UTC 12:00
        self.exit_hour = 12        # UTC 12:00
        self.lookback_period = 4   # Bars to define impulse (4 hours)
        self.cooldown_bars = 6     # Cooldown after a trade (6 hours)
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        opens = ctx.opens(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close position at exit_hour if open
        if ctx.has_position():
            if current_hour == self.exit_hour:
                position_direction = ctx.position_direction()
                entry_price = ctx.position["entry_price"]
                current_price = ctx.bar.close
                reason = f"session_exit_time_{self.exit_hour}UTC"
                if position_direction == "long":
                    profit = (current_price - entry_price) / entry_price * 10000
                else: # short
                    profit = (entry_price - current_price) / entry_price * 10000
                return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_hour": current_hour, "current_price": current_price, "profit_bps_at_exit": profit})

        # Entry logic: Only consider entries within the specified session hours
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate session impulse
        # We need at least 'lookback_period' bars leading up to the current bar to define the impulse
        if len(closes) < self.lookback_period + 1:
            return None

        # Calculate the price change over the lookback period
        # Using the close of the previous lookback_period to the current close
        price_change = closes[-1] - closes[-(self.lookback_period + 1)]
        
        # Calculate recent volatility (e.g., using ATR or simply range)
        recent_range = max(highs[-self.lookback_period:]) - min(lows[-self.lookback_period:])
        
        # Only consider trades if there's significant impulse and not too much chop
        # Impulse threshold: 0.1% change over the lookback period
        impulse_threshold_pct = 0.15 # 0.15% change
        
        # Volatility check: price change should be at least 50% of the recent range to confirm direction
        # This helps filter out signals in very low volatility or choppy periods
        if recent_range == 0:
            return None
        
        # Entry conditions
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry: price has moved up significantly and current price is above the average of the session
        if price_change / closes[-(self.lookback_period + 1)] * 100 > impulse_threshold_pct and \
           abs(price_change) / recent_range > 0.5: # Directional conviction
            if not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7
                reason = "european_morning_long_impulse_confirmed"
        
        # Short entry: price has moved down significantly and current price is below the average of the session
        elif price_change / closes[-(self.lookback_period + 1)] * 100 < -impulse_threshold_pct and \
             abs(price_change) / recent_range > 0.5: # Directional conviction
            if not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7
                reason = "european_morning_short_impulse_confirmed"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "price_change_pct": price_change / closes[-(self.lookback_period + 1)] * 100,
                    "recent_range": recent_range,
                    "current_hour": current_hour,
                    "entry_price": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to current_bar_index to enforce cooldown
        # This ensures the cooldown starts immediately after a position closes for any reason
        self.last_trade_bar = ctx.bar_index
        pass # No adaptive logic for now, just enforces cooldown