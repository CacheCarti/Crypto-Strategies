from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondayRebuild(Strategy):
    METADATA = {
        "name": "SolMondayRebuild",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours, allowing for some rebuild time
        "warmup_bars": 72,              # For 72 bars (3 days) of 1h data
        "required_features": [],        # No specific external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24  # Cooldown for 24 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.weekend_change_threshold = 0.015 # 1.5% move over weekend
        self.rebuild_confirmation_period = 6 # Look back 6 bars for confirmation
        self.rebuild_confirmation_threshold = 0.005 # 0.5% move in confirmation period
        self.last_weekend_close = None
        self.monday_open_price = None

    def _get_weekend_change(self, ctx: BarContext) -> Optional[float]:
        # Find Friday's last close and Monday's first open
        # We need at least 72 bars for a full weekend + Monday
        if len(ctx.closes(72)) < 72:
            return None

        friday_close = None
        monday_open = None

        # Iterate backwards to find Friday's last close and Monday's first open
        for i in range(1, 72): # Up to 72 bars (3 days)
            bar_index = ctx.bar_index - i
            if bar_index < 0:
                continue

            timestamp = ctx.bar.timestamp - i * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(minute=0, second=0, microsecond=0) - ctx.bar.timestamp.replace(minute=0, second=0, microsecond=0)).total_seconds() / 3600)

            # This is a simplification; a real engine would provide historical timestamps
            # For backtesting, we'll assume ctx.bar.timestamp is the current bar's timestamp
            # and infer previous bars' timestamps.
            
            # Simplified logic for finding a "Friday close" and "Monday open"
            # This is tricky with discrete bars and no direct historical timestamp access.
            # We'll use a heuristic based on the current bar's weekday.

            # If current bar is Monday, try to find a previous Friday close
            if ctx.bar.timestamp.weekday() == 0: # Monday
                # Look for the last close before Monday. If the market is open 24/7, this is less clear.
                # Assuming typical market behavior, we'll look for Sunday's last close as proxy for weekend end
                # and Friday's last close as proxy for weekend start.

                # Let's try to detect the start of Monday and the end of Friday
                # (assuming a 24/7 market, so Friday/Sunday close is just the last bar of that day)
                
                # If we are on Monday and haven't recorded last_weekend_close yet
                if ctx.bar.timestamp.weekday() == 0 and self.last_weekend_close is None:
                    # Find the close price from the last bar of the previous Friday (or Sunday if 24/7)
                    # This is a very rough approximation.
                    # We need to look back at least 24 * 3 = 72 bars to cover a full weekend.
                    # Let's say Friday close is 72 bars ago and Monday open is the current open for this heuristic
                    
                    # This is too complex to reliably implement with only ctx.bar.timestamp.weekday()
                    # and requiring precise Friday/Monday start/end.
                    # For simplicity, let's assume we capture the 'weekend move' as the difference
                    # between the close 48 hours ago (approx Friday close) and the current Monday open.
                    
                    # This is a significant simplification from the original request's "Friday's last close and Monday's first open"
                    # without explicit bar timestamps for past bars.
                    
                    # Let's just use the close 48 bars ago (approx start of weekend) vs now (Monday open)
                    # This will not be precise but will capture a 'weekend' move.
                    if len(ctx.closes(48)) >= 48 and len(ctx.opens(1)) >= 1:
                        self.last_weekend_close = ctx.closes(48)[0]
                        self.monday_open_price = ctx.opens(1)[0]
                        if self.last_weekend_close > 0 and self.monday_open_price > 0:
                            return (self.monday_open_price - self.last_weekend_close) / self.last_weekend_close
        return None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        
        if len(closes) < self.warmup_bars:
            return None

        # Reset weekend tracking on Tuesday to prepare for next weekend
        if ctx.bar.timestamp.weekday() == 1: # Tuesday
            self.last_weekend_close = None
            self.monday_open_price = None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only trade on Monday during US market hours (e.g., 9 AM to 5 PM UTC)
        # Assuming US market open translates to ~13:00 - 21:00 UTC
        if ctx.bar.timestamp.weekday() == 0 and 13 <= ctx.bar.timestamp.hour < 21:
            weekend_move_pct = self._get_weekend_change(ctx)

            if weekend_move_pct is not None:
                # Check for a significant weekend move
                if abs(weekend_move_pct) > self.weekend_change_threshold:
                    # Check for rebuild confirmation during US hours on Monday
                    # Look for a move in the opposite direction of the weekend move
                    # If weekend was down, look for Monday up-move confirmation
                    # If weekend was up, look for Monday down-move confirmation
                    
                    if len(closes) < self.rebuild_confirmation_period + 1:
                        return None
                    
                    current_price = closes[-1]
                    price_at_start_of_confirmation = closes[-self.rebuild_confirmation_period - 1]
                    
                    if price_at_start_of_confirmation == 0: # Avoid division by zero
                        return None

                    rebuild_move_pct = (current_price - price_at_start_of_confirmation) / price_at_start_of_confirmation

                    # Long signal: Weekend was significantly down, Monday is rebuilding up
                    if weekend_move_pct < -self.weekend_change_threshold and \
                       rebuild_move_pct > self.rebuild_confirmation_threshold and \
                       not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            metadata={
                                "reason": "monday_rebuild_long",
                                "weekend_move_pct": weekend_move_pct,
                                "rebuild_move_pct": rebuild_move_pct,
                                "current_price": current_price,
                                "monday_hour_utc": ctx.bar.timestamp.hour
                            }
                        )

                    # Short signal: Weekend was significantly up, Monday is rebuilding down
                    elif weekend_move_pct > self.weekend_change_threshold and \
                         rebuild_move_pct < -self.rebuild_confirmation_threshold and \
                         not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            metadata={
                                "reason": "monday_rebuild_short",
                                "weekend_move_pct": weekend_move_pct,
                                "rebuild_move_pct": rebuild_move_pct,
                                "current_price": current_price,
                                "monday_hour_utc": ctx.bar.timestamp.hour
                            }
                        )
        
        # If we have a position, hold it until TP/SL or horizon
        if ctx.has_position():
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, potentially adjust parameters
        pass