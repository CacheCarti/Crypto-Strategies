from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaRecovery(Strategy):
    METADATA = {
        "name": "SOL Asia Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # Look back 24 bars (1 day) for price action
        self.flush_threshold_pct = -3.0 # % drop to consider a flush
        self.recovery_threshold_pct = 0.5 # % recovery from low to confirm stabilization
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # State variables for detecting the flush and recovery
        self.flush_detected_bar = -1
        self.flush_low_price = math.inf
        self.flush_start_price = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period)
        lows = ctx.lows(self.lookback_period)
        highs = ctx.highs(self.lookback_period)

        if len(closes) < self.lookback_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # --- Flush Detection (UTC 0-8) ---
        # Look for a sharp drop within the last N bars ending in the UTC 0-8 window
        if 0 <= current_hour <= 8:
            # Check for a significant drop within the last few bars
            # Let's define a "sharp drop" as current price being significantly lower than a recent high
            # and the previous bar's close also being significantly lower than its open.
            # This is a simplified approach. A more robust one might involve ATR or price change over X bars.

            # Simple flush detection: if current close is significantly lower than max high in last few bars
            # and it's a red candle.
            
            # Look for a drop within a shorter window (e.g., last 5 bars) for the "flush"
            recent_high = max(highs[-min(5, len(highs)):])
            price_drop_pct = ((current_close - recent_high) / recent_high) * 100 if recent_high > 0 else 0

            # Check if this bar itself is a significant red candle
            is_red_candle = ctx.bar.close < ctx.bar.open
            candle_drop_pct = ((ctx.bar.close - ctx.bar.open) / ctx.bar.open) * 100 if ctx.bar.open > 0 else 0

            if price_drop_pct < self.flush_threshold_pct and is_red_candle and candle_drop_pct < self.flush_threshold_pct / 2:
                # If a flush is detected, record its details
                if self.flush_detected_bar == -1 or current_close < self.flush_low_price:
                    self.flush_detected_bar = ctx.bar_index
                    self.flush_low_price = current_close
                    # The flush start price could be the high of the bar where the flush started
                    self.flush_start_price = recent_high # Approximation
                return None # Don't trade during the flush

        # --- Recovery Detection (after flush, before UTC 12) ---
        # If a flush was detected and we are now in the recovery window (after the flush, before UTC 12)
        if self.flush_detected_bar != -1 and self.flush_detected_bar < ctx.bar_index and current_hour < 12:
            # Check for stabilization: current price holds above the flush low
            # and has recovered a certain percentage from the flush low.
            if current_close > self.flush_low_price:
                recovery_pct = ((current_close - self.flush_low_price) / self.flush_low_price) * 100
                
                # Check if the previous bar (the stabilization bar) held the panic low
                # This is a bit tricky to implement perfectly with just BarContext.
                # A simplified approach: the current bar's low is not significantly below the flush low.
                # And we need to ensure the price has moved up from the flush low.
                
                # We need to ensure the recovery is actually happening *after* the flush.
                # Let's say the recovery signal fires if current price is above flush low + threshold
                # and the price has been generally moving up from the flush low.
                
                # Simple stabilization: current close is above the flush low and has recovered
                if recovery_pct >= self.recovery_threshold_pct and not ctx.has_position():
                    # Ensure we haven't already traded this flush recovery
                    # This is handled by self.last_trade_bar + cooldown_bars
                    
                    # Reset flush state after a signal to prevent repeated signals on the same flush
                    self.flush_detected_bar = -1
                    self.flush_low_price = math.inf
                    self.flush_start_price = None
                    self.last_trade_bar = ctx.bar_index

                    # Calculate a dynamic take profit based on the flush depth
                    # Aim for a partial retracement of the flush
                    if self.flush_start_price is not None and self.flush_start_price > self.flush_low_price:
                        flush_depth = self.flush_start_price - self.flush_low_price
                        # Target 30-50% retracement of the flush depth
                        target_price = self.flush_low_price + (flush_depth * ctx.rng.uniform(0.3, 0.5))
                        dynamic_tp_bps = ((target_price - current_close) / current_close) * 10000
                        # Cap dynamic TP to a reasonable max
                        dynamic_tp_bps = min(dynamic_tp_bps, self.METADATA["declared_tp_bps"] * 1.5)
                    else:
                        dynamic_tp_bps = self.METADATA["declared_tp_bps"]

                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=dynamic_tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_flush_recovery_long",
                            "current_price": current_close,
                            "flush_low": self.flush_low_price,
                            "recovery_pct": recovery_pct,
                            "dynamic_tp_bps": dynamic_tp_bps,
                            "entry_hour": current_hour
                        }
                    )
        
        # --- Exit by UTC 21 ---
        # If we have a position and it's getting late in the day (UTC 21 or later)
        if ctx.has_position() and ctx.position_direction() == "long" and current_hour >= 21:
            self.last_trade_bar = ctx.bar_index # Update cooldown
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "time_based_exit_end_of_day",
                    "current_price": current_close,
                    "exit_hour": current_hour
                }
            )

        # Reset flush detection if we pass the window for recovery or if a new day starts
        if current_hour >= 12 and self.flush_detected_bar != -1:
            self.flush_detected_bar = -1
            self.flush_low_price = math.inf
            self.flush_start_price = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: reset state or adjust parameters based on trade outcome
        # For this strategy, the cooldown is handled in on_bar.
        pass