from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionRecovery(Strategy):
    METADATA = {
        "name": "SOL Session Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours typically, but can hold longer
        "warmup_bars": 30,
        "required_features": [], # No external features used for this specific strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_low_price = None
        self.entry_price = None
        self.cooldown_bars = 0
        self.cooldown_duration = 12 # Cooldown for 12 hours after a trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # Exit logic
        if ctx.has_position():
            # Exit by UTC 21 (9 PM UTC)
            if current_hour >= 21 and ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "time_based_exit_utc21",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close
                })

            # Exit at 30% retracement of the flush (from entry)
            # This requires tracking flush_low_price and entry_price
            if self.flush_low_price is not None and self.entry_price is not None:
                # Calculate the potential recovery from the flush low to the entry
                potential_recovery = self.entry_price - self.flush_low_price
                if potential_recovery > 0:
                    # Calculate the target for a 30% retracement
                    retracement_target = self.flush_low_price + (potential_recovery * 0.3)
                    if ctx.bar.close <= retracement_target and ctx.position_direction() == "long":
                        return ctx.signal("flat", confidence=0.7, metadata={
                            "reason": "partial_retracement_exit",
                            "flush_low": self.flush_low_price,
                            "entry_price": self.entry_price,
                            "current_price": ctx.bar.close,
                            "retracement_target": retracement_target
                        })
            return None # Hold position if no exit conditions met

        # Entry logic
        # Identify sharp drop during UTC 0-8
        # Define "sharp drop": e.g., 3-bar drop of more than 3%
        # Or, significant drop below a short-term SMA (e.g., 5-bar SMA)
        sma_short_period = 5
        sma_short = self._sma(closes, sma_short_period)

        if sma_short is None:
            return None

        # Condition 1: Time window for flush (UTC 0-8)
        is_flush_window = 0 <= current_hour <= 8

        # Condition 2: Price drop (e.g., current close significantly below a recent high, or 3-bar % drop)
        # For simplicity, let's look for a significant drop from the open of the current bar relative to previous close
        # or a significant negative change over the last few bars
        recent_max_price = max(highs[-5:])
        recent_min_price = min(lows[-5:])
        drop_percentage = ((recent_max_price - ctx.bar.close) / recent_max_price) * 100 if recent_max_price > 0 else 0

        # We're looking for a sharp drop, so current close should be near recent lows
        is_significant_drop = drop_percentage > 2.0 and ctx.bar.close <= (recent_min_price * 1.01) # Close near 5-bar low

        # Condition 3: Stabilization before UTC 12 (i.e., between UTC 9-11)
        # And the stabilization bar holds the panic low (current low is higher than or equal to the flush low)
        # For this, we need to track the flush low.
        is_stabilization_window = 9 <= current_hour <= 11

        # Track flush low if we are in the flush window and see a drop
        if is_flush_window and is_significant_drop:
            # If a flush is detected, store the low of this bar as a potential panic low
            if self.flush_low_price is None or ctx.bar.low < self.flush_low_price:
                self.flush_low_price = ctx.bar.low
                # Also store the price at the start of the flush for later retracement calculation
                # This could be the open of the bar that started the drop, or the high before the drop
                # For simplicity, let's track the lowest low during the flush window
            return None # Don't trade during flush, wait for stabilization

        # If we are in the stabilization window and a flush low was detected earlier
        if is_stabilization_window and self.flush_low_price is not None:
            # Condition: Stabilization bar holds the panic low
            # This means the current bar's low should not break the flush_low_price
            # And the close should be recovering (e.g., above its open, or above the low of previous bar)
            # Or, current close is above the flush_low_price and showing some strength (e.g. above sma_short)
            current_bar_holds_low = ctx.bar.low >= self.flush_low_price * 0.99 # Allow slight dip, but generally hold
            is_recovering = ctx.bar.close > ctx.bar.open and ctx.bar.close > sma_short

            if current_bar_holds_low and is_recovering:
                # Buy the recovery
                self.entry_price = ctx.bar.close
                self.cooldown_bars = self.cooldown_duration # Start cooldown
                signal = ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_session_recovery_entry",
                        "flush_low_price": self.flush_low_price,
                        "current_close": ctx.bar.close,
                        "current_hour": current_hour,
                        "sma_short_period": sma_short_period,
                        "sma_short_value": sma_short,
                        "recovery_strength": (ctx.bar.close - ctx.bar.open) / ctx.bar.open
                    }
                )
                # Reset flush low after entry to prepare for next cycle
                self.flush_low_price = None
                return signal

        # Reset flush low if we passed the stabilization window without entry
        if current_hour > 11 and self.flush_low_price is not None:
            self.flush_low_price = None

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset relevant state variables after a position closes
        self.entry_price = None
        self.flush_low_price = None # Ensure it's reset to look for a new flush
        self.cooldown_bars = self.cooldown_duration # Apply cooldown after any close