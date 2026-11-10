from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours
        "warmup_bars": 30,
        "required_features": [], # No specific features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_session_close = None
        self.last_session_close_bar_index = -1
        self.traded_today = False
        self.cooldown_until_bar = -1
        self.gap_threshold_bps = 50 # Gap size in bps to consider for trading
        self.continuation_threshold_bps = 20 # How much price should move in gap direction to invalidate
        self.reversion_target_bps = 100 # How much profit to target from reversion

    def _get_sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Reset traded_today flag at the start of a new trading day (e.g., UTC 0)
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False

        # --- Session Boundary Logic ---
        # Define late US session as UTC 18-24 (18, 19, 20, 21, 22, 23)
        # We want to capture the close at the end of this session (hour 23)
        # and compare it to the open of the next session (hour 0 or 1)

        # Capture last session close (at UTC 23)
        if current_hour == 23 and self.last_session_close_bar_index != current_bar_index:
            self.last_session_close = ctx.bar.close
            self.last_session_close_bar_index = current_bar_index

        # Trading window: First few hours of the new session (e.g., UTC 0-3)
        # Only trade if we haven't traded yet today and we have a prior session close
        if 0 <= current_hour <= 3 and not self.traded_today and self.last_session_close is not None:
            if ctx.has_position():
                # If we have a position, we manage it or hold. Don't open new positions.
                return None
            if current_bar_index < self.cooldown_until_bar:
                return None

            session_open = ctx.bar.open
            gap_bps = ((session_open - self.last_session_close) / self.last_session_close) * 10000

            # Check for a significant gap
            if abs(gap_bps) > self.gap_threshold_bps:
                # Check for lack of continuation in the first bar of the new session
                # If gap was up, we expect price to drop. If gap was down, we expect price to rise.
                # Lack of continuation means current close is NOT in direction of gap or is reversing
                
                # If current price has moved significantly *against* the gap, it's a reversion signal
                reversion_condition = False
                signal_direction = "hold"
                reason = "no_signal"
                confidence = 0.0

                if gap_bps > self.gap_threshold_bps: # Gap up
                    # Price opened much higher than prior close. We expect it to revert down.
                    # Check if the current price is moving down or consolidating, not continuing up.
                    # A simple check: current close is below open, or not significantly above open.
                    if ctx.bar.close < session_open: # Price dropped after gap up
                        reversion_condition = True
                        signal_direction = "short"
                        reason = "gap_up_reversion_down"
                        confidence = 0.7
                    elif (ctx.bar.close - session_open) / session_open * 10000 < self.continuation_threshold_bps:
                        # Gap up but little to no continuation upwards
                        reversion_condition = True
                        signal_direction = "short"
                        reason = "gap_up_no_continuation"
                        confidence = 0.6

                elif gap_bps < -self.gap_threshold_bps: # Gap down
                    # Price opened much lower than prior close. We expect it to revert up.
                    # Check if the current price is moving up or consolidating, not continuing down.
                    if ctx.bar.close > session_open: # Price rose after gap down
                        reversion_condition = True
                        signal_direction = "long"
                        reason = "gap_down_reversion_up"
                        confidence = 0.7
                    elif (session_open - ctx.bar.close) / session_open * 10000 < self.continuation_threshold_bps:
                        # Gap down but little to no continuation downwards
                        reversion_condition = True
                        signal_direction = "long"
                        reason = "gap_down_no_continuation"
                        confidence = 0.6

                if reversion_condition:
                    self.traded_today = True # Mark as traded for the day
                    self.cooldown_until_bar = current_bar_index + 5 # Cooldown for 5 bars
                    
                    # Target the prior session close for TP, or a fixed bps amount
                    target_price = self.last_session_close
                    
                    # Calculate dynamic TP and SL based on strategy logic and declared values
                    dynamic_tp_bps = self.declared_tp_bps
                    dynamic_sl_bps = self.declared_sl_bps

                    if signal_direction == "long":
                        # For long, target is higher than entry. If last_session_close is above entry, use it.
                        if target_price > ctx.bar.close:
                            dynamic_tp_bps = max(self.reversion_target_bps, ((target_price - ctx.bar.close) / ctx.bar.close) * 10000)
                        else:
                            dynamic_tp_bps = self.declared_tp_bps # Fallback to default
                        
                        # Set SL below entry, potentially using the session open as a guide
                        dynamic_sl_bps = max(self.declared_sl_bps, ((ctx.bar.close - session_open) / ctx.bar.close) * 10000 * 1.5) # SL wider than the gap
                        
                    elif signal_direction == "short":
                        # For short, target is lower than entry. If last_session_close is below entry, use it.
                        if target_price < ctx.bar.close:
                            dynamic_tp_bps = max(self.reversion_target_bps, ((ctx.bar.close - target_price) / ctx.bar.close) * 10000)
                        else:
                            dynamic_tp_bps = self.declared_tp_bps # Fallback to default

                        # Set SL above entry, potentially using the session open as a guide
                        dynamic_sl_bps = max(self.declared_sl_bps, ((session_open - ctx.bar.close) / ctx.bar.close) * 10000 * 1.5) # SL wider than the gap

                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        stop_loss_bps=dynamic_sl_bps,
                        take_profit_bps=dynamic_tp_bps,
                        horizon_seconds=self.declared_hold_seconds,
                        metadata={
                            "reason": reason,
                            "gap_bps": gap_bps,
                            "last_session_close": self.last_session_close,
                            "session_open": session_open,
                            "current_close": ctx.bar.close,
                            "cooldown_bars": 5
                        }
                    )

        # --- Position Management (Exit before next session close or on structure stop) ---
        if ctx.has_position():
            # If current_hour is 23 (end of session), close position to avoid holding overnight
            if current_hour == 23 and ctx.position_direction() is not None:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "end_of_session_exit",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.cooldown_until_bar = ctx.bar_index + 5 # Ensure cooldown is applied after a close
        pass