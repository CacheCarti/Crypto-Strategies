from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionRecovery(Strategy):
    METADATA = {
        "name": "SOL Session Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Up to 9 hours (9 * 3600 = 32400) but target 2-4 hours
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 8 # Hours for flush detection (0-8 UTC)
        self.stabilization_window = 4 # Hours for stabilization (up to 12 UTC)
        self.exit_hour = 21 # UTC
        self.min_flush_percent = 0.02 # 2% drop
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State to track potential flush
        self.flush_detected_price = None
        self.flush_low_price = None
        self.flush_start_bar = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        lows = ctx.lows(30)
        opens = ctx.opens(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # --- Position Management ---
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos["entry_price"]

            # Exit at end of day
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "entry_price": entry_price,
                    "current_price": ctx.bar.close
                })

            # Exit on partial retracement (e.g., 50% of the flush if we knew it)
            # For simplicity, let's use a dynamic exit based on recent performance or time.
            # If price drops significantly from recent highs while in position, exit.
            # This is a simplified trailing stop logic for strategies that don't use fixed TP/SL effectively.
            if self.flush_low_price is not None:
                # If we've recovered from the flush low and price starts to drop again
                # A simple check: if current price is below 50% of the recovery from flush_low to current_peak
                # This needs a peak tracker, which adds state. Let's simplify.
                # Exit if price falls below entry price after a significant gain
                if ctx.bar.close < entry_price * 0.99 and ctx.bar.close < self._sma(closes, 5) * 0.99:
                     return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "recovery_failure_exit",
                        "entry_price": entry_price,
                        "current_price": ctx.bar.close,
                        "flush_low": self.flush_low_price
                    })

            return None # Hold position

        # --- Entry Logic ---

        # Phase 1: Detect Sharp Drop (UTC 0-8)
        if current_hour >= 0 and current_hour < self.flush_period:
            # Look for a significant drop within the last few bars
            # A simple way: current close significantly below the open of the last few hours
            # Or, current low is significantly lower than previous closes
            recent_high = max(opens[-min(len(opens), self.flush_period):])
            if recent_high is None:
                return None

            flush_candidate_drop = (recent_high - ctx.bar.low) / recent_high
            if flush_candidate_drop >= self.min_flush_percent:
                # This bar might be part of a flush
                if self.flush_detected_price is None: # First detection of potential flush
                    self.flush_detected_price = recent_high
                    self.flush_low_price = ctx.bar.low
                    self.flush_start_bar = ctx.bar_index
                else: # Update flush low if it continues
                    self.flush_low_price = min(self.flush_low_price, ctx.bar.low)
            elif self.flush_detected_price is not None:
                # If price recovers significantly during the flush window, reset
                if ctx.bar.close > self.flush_detected_price * (1 - self.min_flush_percent / 2):
                    self.flush_detected_price = None
                    self.flush_low_price = None
                    self.flush_start_bar = None

        # Phase 2: Stabilization and Recovery (before UTC 12)
        if self.flush_detected_price is not None and self.flush_low_price is not None:
            if current_hour >= self.flush_period and current_hour < self.stabilization_window + self.flush_period: # 8 to 12 UTC
                # Conditions for stabilization:
                # 1. Current bar must close above its open (green candle)
                # 2. Current bar's low must be above the flush_low_price (holding the low)
                # 3. Price should show some recovery from the absolute low
                # 4. SMA crossover or price above short SMA to confirm recovery
                
                # Check for sufficient flush depth before considering recovery
                actual_flush_depth = (self.flush_detected_price - self.flush_low_price) / self.flush_detected_price
                if actual_flush_depth < self.min_flush_percent:
                    # Flush wasn't significant enough, reset
                    self.flush_detected_price = None
                    self.flush_low_price = None
                    self.flush_start_bar = None
                    return None

                if ctx.bar.close > ctx.bar.open and \
                   ctx.bar.low >= self.flush_low_price * 0.995 and \
                   ctx.bar.close > self.flush_low_price * 1.005: # At least 0.5% recovery from low

                    # Additional confirmation: price above a short-term moving average
                    sma_short = self._sma(closes, 5)
                    if sma_short is not None and ctx.bar.close > sma_short:
                        # Signal long
                        self.last_trade_bar = ctx.bar_index
                        # Reset flush state after signal
                        flush_detected_at_signal = self.flush_detected_price
                        flush_low_at_signal = self.flush_low_price
                        self.flush_detected_price = None
                        self.flush_low_price = None
                        self.flush_start_bar = None

                        return ctx.signal(
                            "long",
                            confidence=0.8,
                            metadata={
                                "reason": "sol_session_recovery_buy",
                                "flush_detected_price": flush_detected_at_signal,
                                "flush_low_price": flush_low_at_signal,
                                "current_close": ctx.bar.close,
                                "current_hour": current_hour,
                                "sma5": sma_short
                            }
                        )
            elif current_hour >= self.stabilization_window + self.flush_period: # After 12 UTC, too late to enter
                self.flush_detected_price = None
                self.flush_low_price = None
                self.flush_start_bar = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing for analysis, no new signals from here
        pass