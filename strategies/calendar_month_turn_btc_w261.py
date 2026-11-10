from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMomentum(Strategy):
    METADATA = {
        "name": "Month-End Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours * 3600 = 7200, for 1h bars, this is around 2 bars
        "warmup_bars": 48, # Need enough bars for 24-hour lookback
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 24 # Cooldown for 24 hours after a trade
        self.last_month_end_bar = -self.METADATA["warmup_bars"]
        self.month_end_window = 24 # Look back 24 hours for daily direction
        self.month_start_window = 24 # Look forward 24 hours for daily direction
        self.min_return_pct = 0.01 # 1% daily move to be considered decisive
        self.min_confidence = 0.6
        self.max_confidence = 0.8
        self.entry_cooldown_bars = 48 # Wait 48 hours before another month-end trade
        
        # Track if we've already traded this month-end period
        self.traded_this_month_transition = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_timestamp = ctx.bar.timestamp
        current_month = current_timestamp.month
        current_day = current_timestamp.day

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None
        
        # Check if we are in a new month transition period
        is_month_transition = False
        if current_day == 1 and not self.traded_this_month_transition:
            # It's the first day of the month, check previous month's last day
            prev_month_ts = current_timestamp.replace(day=1) - ctx.market.get("bar_duration", 3600) # Go back to prev bar, then find last day
            prev_month_ts = prev_month_ts.replace(day=1) # Go to start of prev month
            prev_month_last_day = (current_timestamp.replace(day=1) - ctx.market.get("bar_duration", 3600)).day
            
            # Check if we are still within the month-start window (first 24 bars of the month)
            if current_timestamp.day == 1 and current_timestamp.hour < self.month_start_window:
                is_month_transition = True
        
        elif current_day > 20: # Only check for month-end in the last ~10 days
            # Check if next bar would be the start of a new month
            next_timestamp = current_timestamp + ctx.market.get("bar_duration", 3600)
            if next_timestamp.month != current_month and not self.traded_this_month_transition:
                # We are near month-end, and next bar is new month
                # Check if we are still within the month-end window (last 24 bars of the month)
                if current_timestamp.hour >= 24 - self.month_end_window: # Placeholder, needs refinement for actual month-end
                    is_month_transition = True
        
        # If we are not in a month transition period, reset the flag
        if not is_month_transition and self.traded_this_month_transition:
            self.traded_this_month_transition = False # Reset for next month

        if not is_month_transition:
            return None

        # Ensure enough data for 24-hour return calculations
        if len(closes) < 24:
            return None

        # Calculate 24-hour returns for decisiveness
        btc_24h_return = ctx.features.get("btc_return_pct", 0.0) / 100.0 # Convert % to decimal

        signal_direction = None
        reason = "no_decisive_move"
        confidence = 0.0
        
        # Long condition: Strong positive momentum entering the new month
        if current_day == 1 and btc_24h_return > self.min_return_pct:
            signal_direction = "long"
            reason = "month_start_bullish_momentum"
            confidence = self.min_confidence + (btc_24h_return / self.min_return_pct) * (self.max_confidence - self.min_confidence)
            confidence = min(confidence, self.max_confidence)

        # Short condition: Strong negative momentum entering the new month
        elif current_day == 1 and btc_24h_return < -self.min_return_pct:
            signal_direction = "short"
            reason = "month_start_bearish_momentum"
            confidence = self.min_confidence + (abs(btc_24h_return) / self.min_return_pct) * (self.max_confidence - self.min_confidence)
            confidence = min(confidence, self.max_confidence)
        
        # If we have a position, and we are exiting the month-transition window, flatten
        if ctx.has_position() and not is_month_transition and ctx.position_direction() is not None:
             self.last_trade_bar = ctx.bar_index
             return ctx.signal("flat", confidence=0.5, metadata={"reason": "month_transition_window_ended", "btc_24h_return": btc_24h_return})

        if signal_direction and not ctx.has_position() and not self.traded_this_month_transition:
            self.last_trade_bar = ctx.bar_index
            self.traded_this_month_transition = True # Mark that we've traded this transition
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "btc_24h_return_pct": btc_24h_return * 100,
                    "current_month": current_month,
                    "current_day": current_day
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset trade flag if position closed, allowing a new trade in a future month transition
        # But for this strategy, we only allow one trade per transition, so we rely on the in-bar check.
        pass