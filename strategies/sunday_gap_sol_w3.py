from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyTransitionStrategy(Strategy):
    METADATA = {
        "name": "Weekly Transition Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours target
        "warmup_bars": 50,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 6  # Wait 6 bars (6 hours) after a trade
        self.weekly_transition_period = 3  # Look at the first 3 bars after Sunday UTC 00:00
        self.volume_ratio_threshold = 1.5 # Volume surge relative to average
        self.min_move_pct = 0.5 # Minimum price movement in percent to consider decisive
        self.avg_volume_period = 24 # For average volume calculation

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)
        volumes = ctx.volumes(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit existing position before Monday UTC 21:00
        if ctx.has_position() and ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour >= 21:
            if ctx.position_direction() == "long" or ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "monday_eod_exit", "current_price": ctx.bar.close})

        # Logic for Sunday UTC 00:00 transition
        # Check if it's Sunday 00:00 UTC (start of the week) or shortly after
        is_sunday_midnight_utc = (ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == 0)
        is_monday_morning_utc = (ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour < self.weekly_transition_period)

        if not is_sunday_midnight_utc and not is_monday_morning_utc:
            return None # Only interested in the very start of the week

        # Only trigger once at the start of the week for the first few bars
        if ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour == 0 and ctx.bar_index == self.last_trade_bar + 1:
            return None # Already processed this weekly opening or just traded.

        if ctx.has_position():
            return None

        # Look at the first few bars of the new week
        # We need enough bars to determine the initial move
        required_bars_for_analysis = self.weekly_transition_period + 1
        if len(closes) < required_bars_for_analysis:
            return None

        # Calculate average volume over a longer period for comparison
        avg_volume = self._sma(volumes, self.avg_volume_period)
        if avg_volume is None or avg_volume == 0:
            return None

        # Analyze the initial move
        # We look at the first 'weekly_transition_period' bars starting from Sunday UTC 00:00
        # If Sunday 00:00 is the current bar, we look at current + next 2. If it's Sunday 01:00, we look at current + next 1.
        # To simplify, we'll just check if the current bar is within the first few hours of Monday UTC.
        # This implicitly captures the "first decisive move" after Sunday 00:00.

        # Identify the start of the "transition window"
        start_of_window_bar_idx = -1
        for i in range(len(closes) - 1, -1, -1):
            bar_ts = ctx.bar.timestamp
            # Adjust timestamp for historical data if needed, but ctx.bar.timestamp should be UTC
            # We are looking for the Sunday 00:00 UTC bar.
            # For simplicity, let's just make sure we are evaluating within the first few hours of Monday
            # And that we have enough history to cover the transition window.
            if bar_ts.weekday() == 0 and bar_ts.hour < self.weekly_transition_period: # Monday 00, 01, 02 UTC
                start_of_window_bar_idx = ctx.bar_index - (len(closes) - 1 - i)
                break
        
        if start_of_window_bar_idx == -1:
            return None # Not in the specified weekly transition window

        # Ensure we have enough bars for the full transition period starting from Sunday 00:00
        # The logic here is to look at the bars *since* Sunday 00:00 UTC.
        # We'll use the price change from the Sunday 00:00 UTC close to the current bar's close.
        
        # Find the close of Sunday 00:00 UTC (or the closest available bar around that time)
        # For simplicity, let's make the entry trigger on Monday in the early hours.
        # We need the close of the previous week's last bar (Sat 23:00 UTC) or Sunday 00:00 UTC open.
        # Let's say we look at the change from Sunday 00:00 UTC open to the current bar's close.
        # This is a bit tricky with `ctx.closes` which is relative to the current bar.
        
        # Let's re-interpret: we want to capture the initial direction and strength of the move
        # after the market "reopens" for the week. The easiest way is to look at the first few bars
        # on Monday morning UTC.

        # If it's Monday 00:00 UTC, or Monday 01:00 UTC, etc. (within self.weekly_transition_period hours)
        if ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour < self.weekly_transition_period:
            # We need to ensure we haven't already traded this week's opening
            if self.last_trade_bar > ctx.bar_index - self.weekly_transition_period:
                return None # Already traded this transition window

            # Look back to the open of the first bar on Monday (or Sunday 00:00 UTC if that's where the week starts)
            # For `sol_usdc` 1h bars, Sunday 00:00 UTC is the start of the week.
            # We will use `opens[-self.weekly_transition_period]` as the reference point if available,
            # or simply the previous bar's close as a proxy for the 'start of the move'.
            
            # Let's define the start of the weekly move as the close of the bar just before Sunday 00:00 UTC,
            # or the open of Sunday 00:00 UTC.
            # Given ctx.closes returns oldest first, closes[0] is the oldest.
            # We need to find the close of the bar that represents the "beginning of the week's first significant move".
            
            # Let's simplify: We are interested in the move from the close of the bar at Sunday 00:00 UTC,
            # to the current bar's close, provided we are still within the first few hours of Monday.
            
            # Find the Sunday 00:00 UTC bar close. This is hard with relative indexing.
            # Instead, let's look for a decisive move *within* the first `weekly_transition_period` bars of Monday.
            # This means we'll use the open of the first bar of Monday UTC as the reference.
            
            # The strategy says "first decisive move after Sunday UTC reopen".
            # Let's take the close of the bar at Sunday 23:00 UTC as the "reopen" reference.
            # And compare it to the close of the current bar (which is Monday 00:xx UTC).
            
            # This requires a bit of look-back, which is difficult with dynamic bar context.
            # Alternative: measure the move of the current bar relative to its open,
            # and check if it's within the first few hours of Monday.
            
            # Let's try to capture the overall move from the beginning of Sunday 00:00 UTC to the current bar,
            # if the current bar is within the first self.weekly_transition_period hours of Monday.
            
            # We need the close of the bar before Sunday 00:00 UTC to measure the weekly open gap.
            # This is hard to access directly.

            # SIMPLIFICATION: Look for a strong directional bar (or sequence of bars)
            # that happens at the start of Monday UTC, with elevated volume.
            
            # Price change from the open of the first bar of the current "transition window" (Monday 00:00 UTC)
            # to the current bar's close.
            
            # For the purpose of this strategy, we'll define the "first decisive move"
            # as the overall price change from the open of ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            # on Monday, to the current bar's close.
            
            # This means we need the open price of the bar at Monday 00:00 UTC.
            # We can't directly get a specific historical bar by timestamp from ctx.
            
            # Let's use the simplest interpretation: when the bar is Monday 00:00 UTC,
            # we check the close of that bar against its open.
            # If it's Monday 01:00 UTC, check its close against Monday 00:00 UTC's close.
            # This makes the "decisive move" relative to the previous bar.
            
            # To capture the *first* decisive move after Sunday UTC reopen:
            # We will use the bar at Monday 00:00 UTC as the anchor.
            # If the current bar is Monday 00:00 UTC:
            if ctx.bar.timestamp.weekday() == 0 and ctx.bar.timestamp.hour == 0:
                # Check the move of this bar
                price_change_pct = (ctx.bar.close - ctx.bar.open) / ctx.bar.open * 100
                current_volume = ctx.bar.volume
                
                if abs(price_change_pct) >= self.min_move_pct:
                    # Check volume support: current bar's volume vs. average
                    if current_volume > avg_volume * self.volume_ratio_threshold:
                        # Not a single spike bar (this is hard to confirm without more bars,
                        # but high volume and sustained move over a few bars would confirm.
                        # For now, we assume a strong single bar with volume is decisive enough).
                        
                        direction = ""
                        if price_change_pct > 0:
                            direction = "long"
                        elif price_change_pct < 0:
                            direction = "short"

                        if direction:
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                direction,
                                confidence=0.75, # High confidence for a strong weekly open
                                metadata={
                                    "reason": f"weekly_open_decisive_move_{direction}",
                                    "open_price": ctx.bar.open,
                                    "close_price": ctx.bar.close,
                                    "price_change_pct": round(price_change_pct, 2),
                                    "bar_volume": current_volume,
                                    "avg_volume": round(avg_volume, 2),
                                    "volume_ratio": round(current_volume / avg_volume, 2),
                                    "timestamp": str(ctx.bar.timestamp)
                                }
                            )
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after position closes
        self.last_trade_bar = ctx.bar_index
        # Log details about the closed position for analysis
        # print(f"Position closed: {position['direction']} from {position['entry_price']} to {position['exit_price']} "
        #       f"Return: {position['return_bps']} bps, Held: {position['bars_held']} bars.")