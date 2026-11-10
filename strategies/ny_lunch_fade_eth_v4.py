from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class IntradayLiquidityPause(Strategy):
    METADATA = {
        "name": "Intraday Liquidity Pause",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.entry_cooldown_bars

        self.morning_move_period = 4 # 13-16 UTC (4 hours)
        self.fade_window_start_hour = 16
        self.fade_window_end_hour = 19
        self.exit_latest_hour = 21

        self.min_move_pct = 0.5 # Minimum 0.5% move in the morning
        self.max_volatility_regime = "volatile" # Do not trade in crisis
        self.max_crisis_score = 0.5 # Max crisis score to trade

        # Store morning move details
        self.morning_open_price = None
        self.morning_high_price = None
        self.morning_low_price = None
        self.morning_move_direction = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if len(ctx.closes(self.METADATA["warmup_bars"])) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Regime filter
        if ctx.market.get("regime", "normal") in ["crisis", "meltdown"] or ctx.market.get("crisis_score", 0.0) > self.max_crisis_score:
            self.morning_open_price = None # Reset if we enter a crisis
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # --- Position Management ---
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit by latest hour
            if current_hour >= self.exit_latest_hour:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "time_based_exit", "exit_hour": current_hour, "price": current_price})

            # Exit if half the morning move retraces
            if self.morning_open_price is not None and self.morning_move_direction is not None:
                morning_move_size = abs(self.morning_open_price - (self.morning_high_price if self.morning_move_direction == "long" else self.morning_low_price))
                if morning_move_size > 0:
                    retrace_threshold = self.morning_open_price + (morning_move_size / 2) if self.morning_move_direction == "long" else self.morning_open_price - (morning_move_size / 2)

                    if (pos_dir == "short" and current_price >= retrace_threshold) or \
                       (pos_dir == "long" and current_price <= retrace_threshold):
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("flat", confidence=0.7, metadata={"reason": "half_morning_move_retrace_exit", "retrace_threshold": retrace_threshold, "price": current_price, "morning_move_dir": self.morning_move_direction})
            
            return None # Hold position if no exit condition met

        # --- Morning Move Detection (UTC 13-16) ---
        # Start of morning period (UTC 13)
        if current_hour == 13 and self.morning_open_price is None:
            self.morning_open_price = ctx.bar.open
            self.morning_high_price = ctx.bar.high
            self.morning_low_price = ctx.bar.low
            self.morning_move_direction = None
        # During morning period (UTC 13-16)
        elif 13 <= current_hour < 16 and self.morning_open_price is not None:
            self.morning_high_price = max(self.morning_high_price, ctx.bar.high)
            self.morning_low_price = min(self.morning_low_price, ctx.bar.low)
        # End of morning period (UTC 16) - calculate move
        elif current_hour == 16 and self.morning_open_price is not None and self.morning_move_direction is None:
            # Calculate total move from open to high/low of the period
            long_move_pct = ((self.morning_high_price - self.morning_open_price) / self.morning_open_price) * 100 if self.morning_open_price else 0
            short_move_pct = ((self.morning_open_price - self.morning_low_price) / self.morning_open_price) * 100 if self.morning_open_price else 0

            if long_move_pct >= self.min_move_pct and long_move_pct > short_move_pct:
                self.morning_move_direction = "long"
            elif short_move_pct >= self.min_move_pct and short_move_pct > long_move_pct:
                self.morning_move_direction = "short"
            else:
                self.morning_open_price = None # Reset if no significant move
                self.morning_high_price = None
                self.morning_low_price = None
                return None # No signal if no significant morning move

        # --- Fade Logic (UTC 16-19) ---
        if self.fade_window_start_hour <= current_hour < self.fade_window_end_hour and self.morning_move_direction is not None:
            # Entry condition: price retraces part of the move during the lull
            # We fade the move, so if morning was long, we short; if morning was short, we long.

            if self.morning_move_direction == "long":
                # Morning was up, look to short if price starts to retrace from the morning high
                # Check if current price is below the morning high and above the morning open
                if current_price < self.morning_high_price and current_price > self.morning_open_price:
                    # Simple confirmation: check if current bar is closing lower than open (for short entry)
                    if ctx.bar.close < ctx.bar.open:
                        self.last_trade_bar = ctx.bar_index
                        self.morning_open_price = None # Reset for next day
                        return ctx.signal("short", confidence=0.65, metadata={
                            "reason": "fade_morning_long_move",
                            "morning_open": self.morning_open_price,
                            "morning_high": self.morning_high_price,
                            "current_price": current_price,
                            "current_hour": current_hour
                        })
            elif self.morning_move_direction == "short":
                # Morning was down, look to long if price starts to retrace from the morning low
                # Check if current price is above the morning low and below the morning open
                if current_price > self.morning_low_price and current_price < self.morning_open_price:
                    # Simple confirmation: check if current bar is closing higher than open (for long entry)
                    if ctx.bar.close > ctx.bar.open:
                        self.last_trade_bar = ctx.bar_index
                        self.morning_open_price = None # Reset for next day
                        return ctx.signal("long", confidence=0.65, metadata={
                            "reason": "fade_morning_short_move",
                            "morning_open": self.morning_open_price,
                            "morning_low": self.morning_low_price,
                            "current_price": current_price,
                            "current_hour": current_hour
                        })
        
        # Reset morning move data at the end of the day or if conditions aren't met
        if current_hour == 23: # Reset at end of UTC day
            self.morning_open_price = None
            self.morning_high_price = None
            self.morning_low_price = None
            self.morning_move_direction = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing for analytics or adaptive sizing
        pass