from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeSessionDrift(Strategy):
    METADATA = {
        "name": "EuropeSessionDrift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # Aim for 2-4 hours, exit by UTC 12 or stop
        "warmup_bars": 48,  # Need enough bars for ATR and median range
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7
        self.entry_hour_end = 10   # UTC 10 (allows entry up to 09:59)
        self.exit_hour = 12        # UTC 12
        self.min_session_move_atr_mult = 0.5 # Minimum session move to be considered decisive
        self.max_single_bar_spike_ratio = 0.6 # Max ratio of session move from single bar
        self.atr_period = 24       # For dynamic range
        self.cooldown_bars = 6     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State to ensure only one entry per session
        self.last_entry_day = -1
        self.last_entry_direction = None

        # For faster trigger variant
        self.fast_trigger_period = 3
        self.fast_trigger_threshold = 0.003 # 0.3% move
        self.last_fast_trigger_dir = None
        self.last_fast_trigger_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period * 2) # Need more for ATR + session lookback
        highs = ctx.highs(self.atr_period * 2)
        lows = ctx.lows(self.atr_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Exit logic: if past exit hour, or if position is held too long
        if ctx.has_position():
            if current_hour >= self.exit_hour and ctx.position_direction() == self.last_entry_direction:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "session_end_exit",
                    "exit_hour": current_hour,
                    "entry_hour": ctx.position["metadata"].get("entry_hour")
                })
            # Structure stop based on significant reversal (simple fast trigger reversal)
            if ctx.position_direction() == "long" and ctx.bar.close < ctx.bar.open and ctx.bar.open - ctx.bar.close > ctx.bar.open * 0.002: # 0.2% down bar
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_structure_stop_reversal_bar",
                    "bar_change_pct": (ctx.bar.close - ctx.bar.open) / ctx.bar.open
                })
            if ctx.position_direction() == "short" and ctx.bar.close > ctx.bar.open and ctx.bar.close - ctx.bar.open > ctx.bar.open * 0.002: # 0.2% up bar
                 return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_structure_stop_reversal_bar",
                    "bar_change_pct": (ctx.bar.close - ctx.bar.open) / ctx.bar.open
                })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Europe Session Entry Logic ---
        if current_day != self.last_entry_day and self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Calculate session open and range
            session_open_price = None
            session_high_price = -math.inf
            session_low_price = math.inf
            session_bars = []

            # Look back to the start of the session (UTC 7)
            for i in range(1, len(closes) + 1):
                bar_idx = len(closes) - i
                bar_ts = ctx.bar.timestamp - (i-1) * (ctx.bar.timestamp - ctx.closes.timestamps[-2]) # Approximate timestamp
                
                # Crude way to check if it's within the current session's entry window
                # This assumes current bar is the latest. We need bars from current day's 7-10 UTC.
                # A more robust check would involve iterating through timestamps if available.
                # For simplicity, we'll assume we're looking at bars within the current day.
                
                # Find the start of the current session's data within the lookback
                # This requires finding the 7 AM bar of the current day
                
                # Simplified: find the open of the current session (7 AM bar)
                # This is tricky without explicit bar timestamps in a loop.
                # We'll approximate by finding the bars since the start of the current entry window.
                
                # Assume we have at least 3 bars since session start (7, 8, 9 AM)
                # This is a bit of a hack. A better way would be to pass full bar objects or timestamps.
                # Given BarContext constraints, we need to infer.
                
                # Let's collect bars within the current entry window (7 AM to current hour)
                # This needs to be done carefully to ensure we're looking at the right bars.
                
                # Re-approach: we need the open price at 7 UTC.
                # If current_hour is 7, session_open is ctx.bar.open.
                # If current_hour is 8, session_open is ctx.opens()[-2] (if 1h bars).
                # If current_hour is 9, session_open is ctx.opens()[-3].
                
                # This requires knowing how many bars ago 7 AM was.
                # If current_hour is 7, we need ctx.bar.open.
                # If current_hour is 8, we need ctx.opens()[-2].
                # If current_hour is 9, we need ctx.opens()[-3].
                
                bars_since_7am = current_hour - self.entry_hour_start
                if bars_since_7am < 0: # Should not happen if current_hour is in range
                    continue
                
                if len(ctx.opens(bars_since_7am + 1)) < bars_since_7am + 1:
                    # Not enough history for this session yet
                    continue

                session_open_price = ctx.opens(bars_since_7am + 1)[0] # Open of the 7 AM bar
                
                # Now calculate range from 7 AM open to current bar's close
                session_high_price = max(ctx.highs(bars_since_7am + 1))
                session_low_price = min(ctx.lows(bars_since_7am + 1))
                
                session_current_price = ctx.bar.close
                
                session_move_abs = abs(session_current_price - session_open_price)
                
                current_atr = self._atr(highs, lows, closes, self.atr_period)
                if current_atr is None:
                    return None

                min_decisive_move = current_atr * self.min_session_move_atr_mult
                
                if session_move_abs > min_decisive_move:
                    # Check for single-bar spike
                    # Max move in one bar since session open
                    max_single_bar_move = 0.0
                    for k in range(bars_since_7am + 1):
                        bar_open = ctx.opens(bars_since_7am + 1)[k]
                        bar_close = ctx.closes(bars_since_7am + 1)[k]
                        max_single_bar_move = max(max_single_bar_move, abs(bar_close - bar_open))

                    if max_single_bar_move / session_move_abs < self.max_single_bar_spike_ratio:
                        # Decisive move, not a spike, and not already in position
                        if not ctx.has_position():
                            direction = "long" if session_current_price > session_open_price else "short"
                            self.last_entry_day = current_day
                            self.last_entry_direction = direction
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(direction, confidence=0.75, metadata={
                                "reason": "europe_session_drift_entry",
                                "session_open_price": session_open_price,
                                "session_current_price": session_current_price,
                                "session_move_abs": session_move_abs,
                                "min_decisive_move_atr": min_decisive_move,
                                "entry_hour": current_hour
                            })
                            
        # --- Faster Trigger Variant (Transition Detection) ---
        # This will only fire if the session logic didn't, or if it's a different day
        if not ctx.has_position() and ctx.bar_index - self.last_fast_trigger_bar > self.fast_trigger_period:
            if len(closes) >= self.fast_trigger_period:
                recent_change = (closes[-1] - closes[-self.fast_trigger_period]) / closes[-self.fast_trigger_period]
                
                if recent_change > self.fast_trigger_threshold: # Upward momentum
                    if self.last_fast_trigger_dir != "long": # Only trigger on transition
                        self.last_fast_trigger_dir = "long"
                        self.last_fast_trigger_bar = ctx.bar_index
                        self.last_trade_bar = ctx.bar_index # Also respect global cooldown
                        return ctx.signal("long", confidence=0.6, metadata={
                            "reason": "fast_trigger_momentum_up",
                            "recent_change_pct": recent_change,
                            "threshold_pct": self.fast_trigger_threshold
                        })
                elif recent_change < -self.fast_trigger_threshold: # Downward momentum
                    if self.last_fast_trigger_dir != "short": # Only trigger on transition
                        self.last_fast_trigger_dir = "short"
                        self.last_fast_trigger_bar = ctx.bar_index
                        self.last_trade_bar = ctx.bar_index # Also respect global cooldown
                        return ctx.signal("short", confidence=0.6, metadata={
                            "reason": "fast_trigger_momentum_down",
                            "recent_change_pct": recent_change,
                            "threshold_pct": self.fast_trigger_threshold
                        })
                else:
                    self.last_fast_trigger_dir = None # Reset if no clear direction

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last entry day if the position was closed for any reason
        # This helps ensure the session entry logic can re-evaluate on subsequent days
        self.last_entry_day = -1
        self.last_entry_direction = None
        self.last_trade_bar = ctx.bar_index # Enforce cooldown after any closure