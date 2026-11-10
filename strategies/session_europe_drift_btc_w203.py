from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionBreakout(Strategy):
    METADATA = {
        "name": "BTC Session Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 48, # For 24-bar range and 24-bar median
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 (European morning starts)
        self.entry_hour_end = 12   # UTC 12 (European morning ends)
        self.exit_hour = 12        # Exit by UTC 12 if still in position
        self.range_period = 24     # Lookback for median range (1 day)
        self.min_move_factor = 1.0 # Multiplier for median range to qualify a move
        self.max_spike_ratio = 0.6 # Max ratio of single bar move to total move
        self.min_move_bars = 2     # Minimum bars for a decisive move
        self.max_move_bars = 6     # Maximum bars for a decisive move (avoid exhaustion)
        self.cooldown_bars = 6     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        n = len(sorted_data)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        else:
            return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.max_move_bars + 1)
        highs = ctx.highs(self.range_period + self.max_move_bars + 1)
        lows = ctx.lows(self.range_period + self.max_move_bars + 1)

        if len(closes) < self.range_period + self.max_move_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: if position open and it's exit hour
        if ctx.has_position() and current_hour == self.exit_hour and ctx.bar.timestamp.minute == 0:
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "exit_hour": self.exit_hour, "current_price": ctx.bar.close})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_end_exit", "exit_hour": self.exit_hour, "current_price": ctx.bar.close})

        # Entry logic: only within specified hours, and if no position
        if not ctx.has_position() and self.entry_hour_start <= current_hour < self.entry_hour_end:
            if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
                return None # Cooldown active

            # Calculate recent daily ranges
            daily_ranges = []
            for i in range(self.range_period):
                # Using 24-hour lookback for range calculation
                lookback_high = max(highs[-(i+1) : -(i+1) + 24]) if i + 24 <= len(highs) else 0
                lookback_low = min(lows[-(i+1) : -(i+1) + 24]) if i + 24 <= len(lows) else float('inf')
                if lookback_high > 0 and lookback_low != float('inf'):
                    daily_ranges.append(lookback_high - lookback_low)
            
            median_range = self._median(daily_ranges)
            if median_range == 0:
                return None

            # Look for a decisive move in the last self.min_move_bars to self.max_move_bars
            for lookback_bars in range(self.min_move_bars, self.max_move_bars + 1):
                if len(closes) < lookback_bars + 1:
                    continue

                start_price = closes[-(lookback_bars + 1)]
                end_price = closes[-1]
                move_abs = abs(end_price - start_price)

                if move_abs > median_range * self.min_move_factor:
                    # Check for single-bar spike
                    max_single_bar_move = 0
                    for i in range(lookback_bars):
                        max_single_bar_move = max(max_single_bar_move, abs(closes[-(i+1)] - closes[-(i+2)]))
                    
                    if max_single_bar_move / move_abs > self.max_spike_ratio:
                        continue # Skip if it's mostly a single-bar spike

                    # Check if the move is within the current session (approximate)
                    # This strategy assumes the "decisive move" happens leading into the entry hour.
                    # If the current bar is within the entry window, we check the move ending at current bar.
                    
                    if end_price > start_price: # Long setup
                        signal_direction = "long"
                        reason = "session_bullish_breakout"
                    else: # Short setup
                        signal_direction = "short"
                        reason = "session_bearish_breakout"
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=0.7,
                        metadata={
                            "reason": reason,
                            "move_bars": lookback_bars,
                            "move_pct": (move_abs / start_price) * 100,
                            "median_range_usd": median_range,
                            "current_price": ctx.bar.close,
                            "entry_hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass