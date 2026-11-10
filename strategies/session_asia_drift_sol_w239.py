from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class AsiaSessionBreakout(Strategy):
    METADATA = {
        "name": "AsiaSessionBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this structure
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_bars = collections.deque(maxlen=8) # 0-7 UTC, 8 bars
        self.trade_cooldown_bars = 0
        self.last_trade_bar_index = -1
        self.min_range_period = 30 # For median range calculation

    def _median(self, lst):
        sorted_lst = sorted(lst)
        n = len(sorted_lst)
        if n % 2 == 0:
            return (sorted_lst[n // 2 - 1] + sorted_lst[n // 2]) / 2
        else:
            return sorted_lst[n // 2]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < 5: # Wait 5 bars after a trade
            return None

        # --- Asia Session Logic (UTC 0 to 7) ---
        if 0 <= current_hour < 8:
            # Store bars for Asia session
            self.asia_session_bars.append({
                "high": ctx.bar.high,
                "low": ctx.bar.low,
                "close": ctx.bar.close,
                "open": ctx.bar.open,
                "hour": current_hour
            })
            return None # Do not trade during Asia session, just collect data

        # --- Post-Asia Session Breakout Logic (UTC 8 onwards) ---
        if current_hour == 8 and len(self.asia_session_bars) == 8 and ctx.bar_index != self.last_trade_bar_index:
            # Only evaluate at the start of UTC 8, and only once per day
            asia_high = max(b["high"] for b in self.asia_session_bars)
            asia_low = min(b["low"] for b in self.asia_session_bars)
            asia_range = asia_high - asia_low

            # Calculate recent median range to ensure decisiveness
            recent_ranges = []
            for i in range(1, self.min_range_period):
                if ctx.highs(i+1) and ctx.lows(i+1):
                    recent_ranges.append(ctx.highs(i+1)[-1] - ctx.lows(i+1)[-1])
            
            if len(recent_ranges) < self.min_range_period -1:
                return None
            
            median_recent_range = self._median(recent_ranges)

            # Check for decisive move, but not an exhausted single-bar spike
            # A "decisive" move means the Asia range is larger than typical recent ranges
            # A "single-bar spike" check means the current bar's range isn't excessively large compared to the Asia range itself
            
            # Minimum Asia range threshold (e.g., 0.5% of price)
            min_decisive_range_usd = ctx.bar.close * 0.005
            
            if asia_range > max(median_recent_range * 1.2, min_decisive_range_usd): # Asia range is at least 20% larger than median and above a minimum
                # Check if current bar is not an extreme spike already exhausting the move
                current_bar_range = ctx.bar.high - ctx.bar.low
                if current_bar_range > asia_range * 0.5: # Current bar shouldn't be more than 50% of the entire Asia range (avoid exhaustion)
                    return None

                # Breakout detection
                if ctx.bar.close > asia_high: # Bullish breakout
                    self.last_trade_bar_index = ctx.bar_index
                    self.asia_session_bars.clear() # Clear for next day
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_bullish_breakout",
                            "asia_high": asia_high,
                            "asia_low": asia_low,
                            "asia_range": asia_range,
                            "median_recent_range": median_recent_range,
                            "current_close": ctx.bar.close,
                            "entry_hour": current_hour
                        }
                    )
                elif ctx.bar.close < asia_low: # Bearish breakout
                    self.last_trade_bar_index = ctx.bar_index
                    self.asia_session_bars.clear() # Clear for next day
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_bearish_breakout",
                            "asia_high": asia_high,
                            "asia_low": asia_low,
                            "asia_range": asia_range,
                            "median_recent_range": median_recent_range,
                            "current_close": ctx.bar.close,
                            "entry_hour": current_hour
                        }
                    )
            self.asia_session_bars.clear() # Clear if no trade to reset for next day

        # --- Exit existing position before UTC 8 next day or if structure fails ---
        if ctx.has_position():
            # Check if it's nearing the end of the trading window before the next Asia session
            # Exit before UTC 8 the next day to avoid holding through the next Asia session
            # This ensures we are always flat going into the Asia session, ready for a new setup
            if current_hour >= 23 or (current_hour == 7 and ctx.position_direction() is not None): # If still holding at 7AM UTC, exit
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "time_based_session_exit",
                        "current_hour": current_hour
                    }
                )

            # Structure stop: if price re-enters the Asia session range significantly
            # For long, if price falls back into lower half of Asia range
            # For short, if price rises back into upper half of Asia range
            if ctx.position_direction() == "long" and ctx.bar.close < (asia_high + asia_low) / 2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "structure_stop_long_reentered_asia_range",
                        "current_close": ctx.bar.close,
                        "asia_high": asia_high,
                        "asia_low": asia_low
                    }
                )
            elif ctx.position_direction() == "short" and ctx.bar.close > (asia_high + asia_low) / 2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "structure_stop_short_reentered_asia_range",
                        "current_close": ctx.bar.close,
                        "asia_high": asia_high,
                        "asia_low": asia_low
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive sizing or state reset needed beyond what's in on_bar
        pass