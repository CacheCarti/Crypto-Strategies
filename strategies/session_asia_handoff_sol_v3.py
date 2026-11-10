from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaHandoffStrategy(Strategy):
    METADATA = {
        "name": "SOL Asia Handoff Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended to hold for one session, ~8 hours
        "warmup_bars": 50, # Need enough bars for EMA and ATR
        "required_features": [], # No external features used in this version
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after an entry attempt
        self.last_entry_bar = -self.entry_cooldown_bars
        self.session_length_bars = 8 # 8 hours for Asia session
        self.impulse_period = 4 # Use 4 bars (4 hours) to determine session impulse
        self.atr_period = 14 # For dynamic stop/take
        self.ema_period = 20 # For trend filtering
        self.last_signal_direction = None # To prevent repeated signals in the same direction
        self.last_signal_bar = -1 # To prevent signaling on every bar

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Cooldown check
        if current_bar_index - self.last_entry_bar < self.entry_cooldown_bars:
            return None

        # Exit logic for existing positions
        if ctx.has_position():
            # If current bar is end of Asia session (UTC 8) or close to it, close position
            # Allow a small buffer before 8 UTC to ensure closure
            if current_hour >= 8: # Exit at or after 8 UTC
                if ctx.position_direction() == "long":
                    reason = "asia_session_end_long_exit"
                else:
                    reason = "asia_session_end_short_exit"
                return ctx.signal("flat", confidence=0.9, metadata={"reason": reason, "current_hour": current_hour})
            return None # Hold position if not time to exit

        # Entry logic: Only look for entries during Asia session (UTC 0-7)
        if not (0 <= current_hour < 8):
            return None

        # Ensure enough bars for impulse calculation
        if len(closes) < self.impulse_period + 1:
            return None

        # Calculate session impulse
        # We need the open of the first bar of the session and the close of the current bar
        # For simplicity, let's look at the price change over the last 'impulse_period' bars within the session
        # And ensure we are past the initial phase of the session (e.g., after 4 hours)
        if current_hour < self.impulse_period -1 : # Only calculate impulse after enough bars have passed in the session
             return None

        # Calculate impulse from the start of the current session slice
        session_start_price = closes[-(self.impulse_period + 1)] # Open of the impulse period
        session_current_price = closes[-1]
        price_change = session_current_price - session_start_price
        
        # Calculate EMA for trend filtering
        ema20 = self._ema(closes, self.ema_period)
        if ema20 is None:
            return None

        # Dynamic Stop Loss / Take Profit based on ATR
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None:
            return None

        # Define dynamic SL/TP in multiples of ATR
        atr_sl_multiplier = 1.5
        atr_tp_multiplier = 2.5
        dynamic_sl_bps = (atr_sl_multiplier * atr_val / closes[-1]) * 10000
        dynamic_tp_bps = (atr_tp_multiplier * atr_val / closes[-1]) * 10000

        # Clamp SL/TP to declared values if they become too wide/tight
        dynamic_sl_bps = max(self.METADATA["declared_sl_bps"] * 0.75, min(self.METADATA["declared_sl_bps"] * 1.5, dynamic_sl_bps))
        dynamic_tp_bps = max(self.METADATA["declared_tp_bps"] * 0.75, min(self.METADATA["declared_tp_bps"] * 1.5, dynamic_tp_bps))

        # Check for impulse and trend alignment
        # Long condition: price moving up within the session, and above EMA
        if price_change > 0 and closes[-1] > ema20:
            if self.last_signal_direction != "long" or current_bar_index != self.last_signal_bar:
                self.last_signal_direction = "long"
                self.last_signal_bar = current_bar_index
                self.last_entry_bar = current_bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    metadata={
                        "reason": "asia_impulse_long_trend_align",
                        "session_price_change": price_change,
                        "current_price": closes[-1],
                        "ema20": ema20,
                        "atr": atr_val,
                        "dynamic_sl_bps": dynamic_sl_bps,
                        "dynamic_tp_bps": dynamic_tp_bps,
                        "current_hour": current_hour
                    }
                )

        # Short condition: price moving down within the session, and below EMA
        elif price_change < 0 and closes[-1] < ema20:
            if self.last_signal_direction != "short" or current_bar_index != self.last_signal_bar:
                self.last_signal_direction = "short"
                self.last_signal_bar = current_bar_index
                self.last_entry_bar = current_bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    metadata={
                        "reason": "asia_impulse_short_trend_align",
                        "session_price_change": price_change,
                        "current_price": closes[-1],
                        "ema20": ema20,
                        "atr": atr_val,
                        "dynamic_sl_bps": dynamic_sl_bps,
                        "dynamic_tp_bps": dynamic_tp_bps,
                        "current_hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal direction after a position closes to allow new signals
        self.last_signal_direction = None
        # You could also use this to adapt self.entry_cooldown_bars or other parameters
        # based on the outcome of the trade (e.g., if return_bps > 0, reduce cooldown)
        pass