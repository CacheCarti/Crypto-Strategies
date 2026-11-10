from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversion(Strategy):
    METADATA = {
        "name": "OvernightGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Around 3 hours
        "warmup_bars": 48, # Need enough bars for 2 days of overnight sessions
        "required_features": [], # No external features needed for this concept
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Cooldown initialization
        self.entry_price = None

        # Parameters for gap logic
        self.min_gap_bps = 50 # Minimum gap size in bps to consider
        self.continuation_bars = 2 # Number of bars to check for continuation after gap
        self.cooldown_bars = 6 # Cooldown period after a trade in bars (6 hours for 1h bars)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Store the overnight close (UTC 0-6 window, so 6 AM UTC is the end of overnight)
        # We define the "overnight close" as the close of the bar ending at 6 AM UTC (hour 5 in 0-23 format)
        # This is the last bar before the "US session" typically starts becoming active
        if current_hour == 5: # The bar from 5:00 to 5:59 UTC
            self.overnight_close = current_close

        # Check for trade conditions only once per day during the "US session" (e.g., 9 AM UTC onwards)
        # and only if there's no open position and cooldown is over
        if not ctx.has_position() and ctx.bar_index > self.last_trade_bar_index + self.cooldown_bars:
            # Entry window: 9 AM UTC to 14 PM UTC (hours 9-13)
            # This is after the overnight session and before the next major session might start influencing
            if 9 <= current_hour <= 13:
                if self.overnight_close is not None:
                    # Look for a significant gap from the overnight close to the current open
                    # We need enough bars for 'continuation_bars' after the overnight close to check for non-continuation
                    if len(opens) > self.continuation_bars:
                        # The open 'continuation_bars' bars ago would be the open after the overnight close
                        # For example, if continuation_bars=2, we check the open at 7 AM UTC (index -2)
                        # and the close at 8 AM UTC (index -1)
                        # We need the close of the bar that ends at 5 AM UTC (self.overnight_close)
                        # and the open of the bar that starts at 6 AM UTC (opens[-self.continuation_bars-1])
                        # and the closes up to the current bar to check for continuation

                        # Let's simplify: check the gap from self.overnight_close to the open of the current bar
                        gap_to_open_bps = ((current_open - self.overnight_close) / self.overnight_close) * 10000

                        # Check for non-continuation in the bars immediately following the gap
                        # We are looking at the current bar, so need to check the previous `continuation_bars` bars
                        # If the gap was upward, we expect price to move down or sideways in `continuation_bars`
                        # If the gap was downward, we expect price to move up or sideways in `continuation_bars`

                        # Check if the market is not too volatile for this strategy
                        if ctx.market.get("regime", "NORMAL") not in ["CRISIS", "HIGH_VOL"]:

                            # Check for a significant upward gap and subsequent lack of upward continuation
                            if gap_to_open_bps > self.min_gap_bps:
                                # Check if price has failed to continue higher in the last 'continuation_bars' bars
                                # i.e., the current price is below the highest price seen since the gap,
                                # or price has moved significantly back towards the overnight close
                                recent_high = max(ctx.highs(self.continuation_bars + 1))
                                if current_close < recent_high * (1 - self.min_gap_bps / 2 / 10000): # Small pullback
                                    self.last_trade_bar_index = ctx.bar_index
                                    self.entry_price = current_close
                                    return ctx.signal(
                                        "short",
                                        confidence=0.6,
                                        metadata={
                                            "reason": "overnight_up_gap_reversion",
                                            "overnight_close": self.overnight_close,
                                            "current_open": current_open,
                                            "gap_bps": round(gap_to_open_bps, 2),
                                            "recent_high": recent_high,
                                            "regime": ctx.market.get("regime"),
                                        }
                                    )

                            # Check for a significant downward gap and subsequent lack of downward continuation
                            elif gap_to_open_bps < -self.min_gap_bps:
                                # Check if price has failed to continue lower in the last 'continuation_bars' bars
                                recent_low = min(ctx.lows(self.continuation_bars + 1))
                                if current_close > recent_low * (1 + self.min_gap_bps / 2 / 10000): # Small bounce
                                    self.last_trade_bar_index = ctx.bar_index
                                    self.entry_price = current_close
                                    return ctx.signal(
                                        "long",
                                        confidence=0.6,
                                        metadata={
                                            "reason": "overnight_down_gap_reversion",
                                            "overnight_close": self.overnight_close,
                                            "current_open": current_open,
                                            "gap_bps": round(gap_to_to_open_bps, 2),
                                            "recent_low": recent_low,
                                            "regime": ctx.market.get("regime"),
                                        }
                                    )

        # Exit logic:
        # 1. If a position is open and it's approaching the end of the "US session" (e.g., 18:00 UTC)
        # 2. Or if price moves significantly against the trade (simple structure stop)
        if ctx.has_position():
            # Exit before the end of the trading day or next major session, e.g., 18:00 UTC
            if current_hour == 17: # The bar from 17:00 to 17:59 UTC
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "end_of_session_exit",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": current_close,
                    }
                )

            # Simple structure stop: if price moves beyond the overnight close in the "wrong" direction
            # For a short position, if price moves above overnight close
            if ctx.position_direction() == "short" and self.overnight_close is not None:
                if current_close > self.overnight_close * (1 + self.min_gap_bps / 2 / 10000): # Moved back above overnight close + buffer
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_structure_stop_hit",
                            "overnight_close": self.overnight_close,
                            "current_price": current_close,
                            "entry_price": ctx.position["entry_price"],
                        }
                    )
            # For a long position, if price moves below overnight close
            elif ctx.position_direction() == "long" and self.overnight_close is not None:
                if current_close < self.overnight_close * (1 - self.min_gap_bps / 2 / 10000): # Moved back below overnight close - buffer
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_structure_stop_hit",
                            "overnight_close": self.overnight_close,
                            "current_price": current_close,
                            "entry_price": ctx.position["entry_price"],
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset overnight close for next daily cycle, or any other state cleanup
        # This will be reset naturally by the 5 AM bar, but good to ensure
        pass