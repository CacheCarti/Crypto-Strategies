from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionOvernightDriftSOL(Strategy):
    METADATA = {
        "name": "SessionOvernightDriftSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0
        self.overnight_end_hour = 6 # UTC 00:00 to 06:00
        self.entry_hour_start = 7 # Enter after overnight session close
        self.entry_hour_end = 12 # Enter up to UTC 12:00
        self.exit_hour = 16 # Exit before US session (e.g., 16:00 UTC)
        self.min_session_move_factor = 0.005 # 0.5% move
        self.range_period = 20 # For median range calculation
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow first trade
        self.last_overnight_close = None
        self.last_overnight_open = None
        self.last_overnight_bar_index = -1
        self.session_move_direction = None

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate recent median range
        ranges = [h - l for h, l in zip(highs[-self.range_period:], lows[-self.range_period:])]
        median_range = self._median(ranges)
        
        # --- Session Tracking ---
        # Detect start of overnight session
        if current_hour == self.overnight_start_hour and ctx.bar_index > self.last_overnight_bar_index:
            self.last_overnight_open = ctx.bar.open
            self.last_overnight_close = None # Reset for new session
            self.session_move_direction = None
            # Mark that we've processed this overnight start
            self.last_overnight_bar_index = ctx.bar_index 

        # Capture overnight close
        if current_hour == self.overnight_end_hour and self.last_overnight_open is not None and self.last_overnight_close is None:
            self.last_overnight_close = ctx.bar.close
            # Evaluate overnight move
            if self.last_overnight_close and self.last_overnight_open:
                session_move_abs = abs(self.last_overnight_close - self.last_overnight_open)
                session_move_pct = session_move_abs / self.last_overnight_open
                
                # Check for decisive move, not single-bar spike (this means the move should be larger than 1 bar's typical range)
                if session_move_pct > self.min_session_move_factor and session_move_abs > median_range * 0.5: # At least half median range
                    if self.last_overnight_close > self.last_overnight_open:
                        self.session_move_direction = "long"
                    elif self.last_overnight_close < self.last_overnight_open:
                        self.session_move_direction = "short"
                    else:
                        self.session_move_direction = None
                else:
                    self.session_move_direction = None
            else:
                self.session_move_direction = None

        # --- Entry Logic ---
        if not ctx.has_position():
            # Apply cooldown
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
                return None

            # Enter only within specific hours after overnight session
            if self.entry_hour_start <= current_hour <= self.entry_hour_end:
                if self.session_move_direction == "long":
                    # Regime filter: Only long if not in a strong bear trend or crisis
                    if ctx.market.get("trend_regime", "neutral") != "bear" and ctx.market.get("regime", "normal") != "CRISIS":
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_bullish_drift_follow",
                                "session_move_pct": (self.last_overnight_close - self.last_overnight_open) / self.last_overnight_open if self.last_overnight_open else 0.0,
                                "median_range": median_range,
                                "regime": ctx.market.get("regime", "normal"),
                                "trend_regime": ctx.market.get("trend_regime", "neutral")
                            }
                        )
                elif self.session_move_direction == "short":
                    # Regime filter: Only short if not in a strong bull trend or crisis
                    if ctx.market.get("trend_regime", "neutral") != "bull" and ctx.market.get("regime", "normal") != "CRISIS":
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_bearish_drift_follow",
                                "session_move_pct": (self.last_overnight_close - self.last_overnight_open) / self.last_overnight_open if self.last_overnight_open else 0.0,
                                "median_range": median_range,
                                "regime": ctx.market.get("regime", "normal"),
                                "trend_regime": ctx.market.get("trend_regime", "neutral")
                            }
                        )
            
        # --- Exit Logic ---
        if ctx.has_position():
            # Exit before next major session (e.g., US session)
            if current_hour == self.exit_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_session_exit",
                        "exit_hour": self.exit_hour,
                        "current_price": ctx.bar.close
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record that a trade just closed, so cooldown can be applied
        self.last_trade_bar_index = ctx.bar_index
        # Reset session direction so we don't re-enter based on old session data
        self.session_move_direction = None