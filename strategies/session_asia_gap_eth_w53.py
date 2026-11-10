from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 hours max
        "warmup_bars": 24, # Need a full day's worth of bars for session logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_close_price = None
        self.last_trade_bar_index = -1
        self.cooldown_bars = 4 # Cooldown for 4 hours after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Logic for setting Asia session close
        # Asia session ends at UTC 8:00. The bar at 7:00-7:59 is the last bar of Asia.
        # We need the close of the 7:00 bar, which is available at 8:00.
        if current_hour == 8: # This is the first bar AFTER Asia session close
            # The close of the previous bar (UTC 7:00-7:59) is the Asia session close
            self.asia_session_close_price = ctx.closes(2)[0] # closes(2)[0] gives close of previous bar
            # Reset last trade for the new day
            self.last_trade_bar_index = -1 
            return None # Don't trade on the session boundary bar

        # Trading logic only during London/NY overlap (UTC 8-16)
        # We want to trade the reversion from the Asia close
        if 8 <= current_hour < 16:
            if self.asia_session_close_price is None:
                return None # No Asia close recorded yet

            # Cooldown check
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
                return None

            current_open = ctx.bar.open
            current_close = ctx.bar.close

            gap_to_asia_close = current_open - self.asia_session_close_price
            gap_bps = (gap_to_asia_close / self.asia_session_close_price) * 10000

            # Define thresholds for a significant gap (e.g., 20 bps)
            min_gap_bps = 20.0 # Minimum gap to consider for a trade

            # Check for continuation (price moving further away from Asia close)
            # and then attempt to fade it back
            
            # Long Reversion: Open significantly below Asia close, and current bar
            # shows some initial move further down, but then reverses up towards Asia close.
            # Entry: current_open is significantly below asia_session_close_price
            # AND the current bar's close is higher than its open (or at least not significantly lower than its open)
            # indicating a bounce from the low.
            if gap_bps < -min_gap_bps: # Significant gap down
                # Check for potential reversal confirmation within the current bar
                # Price attempts to move back towards the Asia close.
                # A simple check: current_close is above current_open or above a certain threshold relative to open.
                # Or, price has moved up from the low of the current bar.
                if current_close > current_open + (ctx.bar.high - ctx.bar.low) * 0.2: # Closed significantly off the low
                    if not ctx.has_position():
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_session_gap_down_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "asia_close": round(self.asia_session_close_price, 2),
                                "current_open": round(current_open, 2),
                                "current_close": round(current_close, 2),
                                "current_hour": current_hour,
                            }
                        )

            # Short Reversion: Open significantly above Asia close, and current bar
            # shows some initial move further up, but then reverses down towards Asia close.
            if gap_bps > min_gap_bps: # Significant gap up
                # Check for potential reversal confirmation within the current bar
                # Price attempts to move back towards the Asia close.
                # A simple check: current_close is below current_open or below a certain threshold relative to open.
                # Or, price has moved down from the high of the current bar.
                if current_close < current_open - (ctx.bar.high - ctx.bar.low) * 0.2: # Closed significantly off the high
                    if not ctx.has_position():
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_session_gap_up_reversion",
                                "gap_bps": round(gap_bps, 2),
                                "asia_close": round(self.asia_session_close_price, 2),
                                "current_open": round(current_open, 2),
                                "current_close": round(current_close, 2),
                                "current_hour": current_hour,
                            }
                        )
        
        # Exit logic: if we have a position and it's getting close to the end of the trading window (UTC 16)
        # or if the gap has largely closed.
        if ctx.has_position():
            if current_hour >= 15: # Close out positions before 16:00 UTC
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_session_exit",
                        "current_hour": current_hour,
                        "position_direction": ctx.position_direction(),
                    }
                )
            
            # Additional exit condition: if the price has moved significantly back towards the Asia close
            # and is now within a smaller band (e.g., 5 bps) of the Asia close.
            if self.asia_session_close_price is not None:
                current_price_to_asia_close_bps = ((ctx.bar.close - self.asia_session_close_price) / self.asia_session_close_price) * 10000
                if abs(current_price_to_asia_close_bps) < min_gap_bps / 2: # Within 10 bps of Asia close
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "gap_largely_closed_exit",
                            "price_to_asia_close_bps": round(current_price_to_asia_close_bps, 2),
                            "asia_close": round(self.asia_session_close_price, 2),
                            "current_close": round(ctx.bar.close, 2),
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close details for analysis
        pass