import math
from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class SessionHandoffImpulse(Strategy):
    METADATA = {
        "name": "Session Handoff Impulse ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold, covering the overnight session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00
        self.exit_hour = 12            # Exit before US session (UTC 12:00)

        self.impulse_period = 6        # Bars for overnight impulse (6 hours for 1h bars)
        self.confirmation_period = 3   # Bars for follow-through confirmation
        self.cooldown_bars = 12        # Cooldown after a trade (12 hours)
        self.last_trade_bar_index = -self.cooldown_bars

        # Indicators
        self.ema_short_period = 10
        self.ema_long_period = 30

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            if current_hour >= self.exit_hour and current_day != ctx.position["entry_timestamp"].day:
                # Exit if past exit hour and not on the same day as entry
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_handoff_exit",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour,
                    "entry_bar": ctx.position["entry_bar_index"],
                    "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]
                })
            # If position was entered in the current session, hold until exit hour or stop/profit
            return None

        # Entry logic: Only consider entering during the overnight window
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            # Need enough bars to calculate overnight impulse
            if len(closes) < self.impulse_period + self.confirmation_period:
                return None

            # Calculate overnight impulse
            overnight_closes = ctx.closes(self.impulse_period + self.confirmation_period)
            overnight_opens = ctx.opens(self.impulse_period + self.confirmation_period)

            # Ensure we have data for the full impulse period within the overnight window
            # This is a simplification; a more robust check would involve checking timestamps
            if len(overnight_closes) < self.impulse_period + self.confirmation_period:
                return None

            # Directional impulse from the overnight session (e.g., last 6 bars within the window)
            overnight_open = overnight_opens[-self.impulse_period]
            overnight_close = overnight_closes[-1]
            impulse_direction = overnight_close - overnight_open

            # Check for follow-through confirmation in the subsequent bars
            # We look at the last 'confirmation_period' bars (after the impulse)
            # This simplified logic assumes the current bar is part of the confirmation,
            # and the impulse was established in the immediately preceding bars.
            # A more precise implementation would track the start of the overnight window.

            # For simplicity, let's say the impulse is derived from the last 'impulse_period' bars,
            # and confirmation is current price vs. the end of impulse.
            # A better approach for 1-hour bars:
            # Identify the actual start of the overnight window (e.g., 00:00 UTC)
            # Calculate impulse from 00:00 to 06:00
            # Then look for confirmation after 06:00 but before the exit hour.

            # Re-thinking for the 1-hour bar context:
            # We are *in* the overnight window (0-6 UTC).
            # The strategy is to establish a position based on the *current* impulse within this window,
            # intending to hold through the handoff.
            # Let's define impulse as the change over the last `impulse_period` bars.
            # And confirmation as price action relative to EMAs.

            current_closes = ctx.closes(self.ema_long_period)
            if len(current_closes) < self.ema_long_period:
                return None

            ema_short = self._ema(current_closes, self.ema_short_period)
            ema_long = self._ema(current_closes, self.ema_long_period)

            if ema_short is None or ema_long is None:
                return None

            # Use fear & greed as a regime filter
            fear_greed_index = ctx.features.get("fear_greed_index", 50)

            # Long entry conditions:
            # 1. Price above short EMA, short EMA above long EMA (bullish trend)
            # 2. Fear & Greed not in extreme fear
            # 3. Current bar's close is higher than open (positive impulse)
            if (closes[-1] > ema_short and ema_short > ema_long and
                fear_greed_index > 20 and # Not Extreme Fear
                ctx.bar.close > ctx.bar.open):
                
                confidence = min(0.6 + (fear_greed_index / 100) * 0.2, 1.0) # Scale confidence with F&G
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "overnight_bullish_impulse_ema_confirm",
                    "current_hour": current_hour,
                    "close": closes[-1],
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "fear_greed_index": fear_greed_index,
                    "entry_bar": ctx.bar_index,
                    "entry_timestamp": ctx.bar.timestamp,
                })

            # Short entry conditions:
            # 1. Price below short EMA, short EMA below long EMA (bearish trend)
            # 2. Fear & Greed not in extreme greed
            # 3. Current bar's close is lower than open (negative impulse)
            elif (closes[-1] < ema_short and ema_short < ema_long and
                  fear_greed_index < 80 and # Not Extreme Greed
                  ctx.bar.close < ctx.bar.open):

                confidence = min(0.6 + (1 - (fear_greed_index / 100)) * 0.2, 1.0) # Scale confidence with F&G
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "overnight_bearish_impulse_ema_confirm",
                    "current_hour": current_hour,
                    "close": closes[-1],
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "fear_greed_index": fear_greed_index,
                    "entry_bar": ctx.bar_index,
                    "entry_timestamp": ctx.bar.timestamp,
                })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index upon close, allowing cooldown to start effectively
        self.last_trade_bar_index = ctx.bar_index
        # Log or adjust based on position outcome if needed
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps in {position['bars_held']} bars.")
        pass