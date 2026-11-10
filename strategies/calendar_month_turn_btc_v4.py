from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMonthStartBTC(Strategy):
    METADATA = {
        "name": "BTC Month End/Start Calendar",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-3 days average hold
        "warmup_bars": 72, # For 3-day lookback for daily direction
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 48 # Cooldown for 2 days after a trade to avoid overlapping events
        self.last_trade_bar = -self.cooldown_bars
        self.month_end_lookback_days = 2
        self.month_start_lookforward_days = 2
        self.daily_close_period = 24 # 1-hour bars, so 24 bars for a day
        self.min_daily_move_pct = 0.5 # Minimum 0.5% move to be considered decisive
        self.max_trade_per_event = 1
        self.trades_this_event = 0
        self.last_event_month = -1 # Track the month of the last event to reset trade count

    def _get_daily_direction(self, closes, period_bars, min_move_pct):
        if len(closes) < period_bars:
            return None, 0.0

        day_start_price = closes[-period_bars]
        day_end_price = closes[-1]
        
        if day_start_price == 0: # Avoid division by zero
            return None, 0.0

        move_pct = ((day_end_price - day_start_price) / day_start_price) * 100.0

        if abs(move_pct) >= min_move_pct:
            if move_pct > 0:
                return "bullish", move_pct
            else:
                return "bearish", move_pct
        return "neutral", move_pct

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_month = ctx.bar.timestamp.month
        current_day = ctx.bar.timestamp.day
        current_hour = ctx.bar.timestamp.hour

        # Reset trades_this_event if a new month has started since the last event
        if current_month != self.last_event_month:
            self.trades_this_event = 0
            self.last_event_month = current_month

        # Only one trade per event
        if self.trades_this_event >= self.max_trade_per_event:
            return None
        
        # Regime filter: avoid trading in crisis or extreme volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN", "HIGH_VOL"]:
            return None
        
        # Check for month-end conditions (last few days of the month)
        is_month_end = False
        if current_month in [1, 3, 5, 7, 8, 10, 12]: # 31-day months
            if current_day >= 31 - self.month_end_lookback_days and current_day <= 31:
                is_month_end = True
        elif current_month in [4, 6, 9, 11]: # 30-day months
            if current_day >= 30 - self.month_end_lookback_days and current_day <= 30:
                is_month_end = True
        elif current_month == 2: # February (approximate for leap year)
            if current_day >= 28 - self.month_end_lookback_days and current_day <= 29:
                is_month_end = True

        # Check for month-start conditions (first few days of the month)
        is_month_start = False
        if current_day >= 1 and current_day <= self.month_start_lookforward_days:
            is_month_start = True

        if not (is_month_end or is_month_start):
            return None

        # Look back for decisive daily direction before month end
        # We need enough bars to cover the lookback days
        required_bars_for_lookback = self.month_end_lookback_days * self.daily_close_period
        if len(closes) < required_bars_for_lookback:
            return None

        # Calculate daily directions for the month-end/start period
        recent_daily_directions = []
        recent_daily_moves = []
        
        # Iterate backwards from current bar to cover the lookback days
        for i in range(1, self.month_end_lookback_days + self.month_start_lookforward_days + 1):
            start_idx = -1 * (i * self.daily_close_period)
            end_idx = -1 * ((i - 1) * self.daily_close_period) if i > 1 else len(closes)

            if abs(start_idx) > len(closes): # Not enough history
                continue

            day_closes = closes[start_idx:end_idx]
            if len(day_closes) < self.daily_close_period:
                continue # Not a full day's worth of data

            direction, move_pct = self._get_daily_direction(day_closes, self.daily_close_period, self.min_daily_move_pct)
            recent_daily_directions.append(direction)
            recent_daily_moves.append(move_pct)

        # Entry logic: Look for strong directional bias around the month transition
        # Example: strong move up leading into month end, or strong move down
        
        if is_month_end and not ctx.has_position():
            # If we see consistent bullish moves before month end, consider long
            if all(d == "bullish" for d in recent_daily_directions if d != "neutral"):
                self.last_trade_bar = ctx.bar_index
                self.trades_this_event += 1
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "month_end_bullish_momentum",
                        "current_month": current_month,
                        "current_day": current_day,
                        "recent_daily_directions": recent_daily_directions,
                        "recent_daily_moves": recent_daily_moves,
                        "price": ctx.bar.close,
                        "regime": regime,
                    }
                )
            # If we see consistent bearish moves before month end, consider short
            elif all(d == "bearish" for d in recent_daily_directions if d != "neutral"):
                self.last_trade_bar = ctx.bar_index
                self.trades_this_event += 1
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "month_end_bearish_momentum",
                        "current_month": current_month,
                        "current_day": current_day,
                        "recent_daily_directions": recent_daily_directions,
                        "recent_daily_moves": recent_daily_moves,
                        "price": ctx.bar.close,
                        "regime": regime,
                    }
                )
        
        # Exit logic: If we have a position and it's no longer month-end/start, or conditions change
        if ctx.has_position():
            # If we are past the month-start window or the momentum reverses, consider exiting
            # For simplicity, exit after a certain number of bars or if market regime changes
            if ctx.bar_index - self.last_trade_bar > self.declared_hold_seconds / ctx.bar_period_seconds() or regime in ["CRISIS", "MELTDOWN"]:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "hold_time_or_regime_exit",
                        "current_month": current_month,
                        "current_day": current_day,
                        "bars_held": ctx.bar_index - self.last_trade_bar,
                        "price": ctx.bar.close,
                        "regime": regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after a position closes
        self.last_trade_bar = ctx.bar_index
        # We don't reset trades_this_event here as it's tied to the calendar event,
        # which is handled at the start of on_bar based on current_month.