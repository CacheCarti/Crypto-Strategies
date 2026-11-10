from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanOpenReversion(Strategy):
    METADATA = {
        "name": "European Open Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Intended hold up to 3 hours
        "warmup_bars": 50, # Need prior day's close and 20-bar EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.gap_threshold_bps = 50 # 0.5% gap
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State for tracking prior session close and daily trade status
        self.last_day_close = None
        self.traded_today = False
        self.current_day = -1 # To track day changes

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the first 'period' values
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day_of_week = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Reset daily trade status and update last_day_close at the start of a new day (e.g., UTC 00:00)
        if current_hour == 0 and ctx.bar_index != self.current_day:
            if len(ctx.closes(2)) >= 2:
                self.last_day_close = ctx.closes(2)[-2] # Prior bar's close is last day's close if it's 00:00
            self.traded_today = False
            self.current_day = ctx.bar_index

        # Exit logic: if position open and it's past European close (UTC 12), or end of week
        if ctx.has_position():
            if current_hour >= 12 or current_day_of_week >= 5: # Exit by 12:00 UTC or before weekend
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position if conditions not met

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: European morning session (UTC 7-12)
        if 7 <= current_hour < 12 and not self.traded_today and self.last_day_close is not None:
            current_open = ctx.bar.open
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            gap_bps = ((current_open - self.last_day_close) / self.last_day_close) * 10000

            # Check for large gap and lack of continuation
            if abs(gap_bps) > self.gap_threshold_bps:
                signal_direction = None
                confidence = 0.0
                reason = ""

                # Bearish gap up, but price is now below EMA (lack of bullish continuation)
                if gap_bps > 0 and current_open > self.last_day_close and ctx.bar.close < ema:
                    signal_direction = "short"
                    confidence = min(0.8, abs(gap_bps) / 100.0) # Scale confidence with gap size
                    reason = "bearish_gap_up_reversion_below_ema"

                # Bullish gap down, but price is now above EMA (lack of bearish continuation)
                elif gap_bps < 0 and current_open < self.last_day_close and ctx.bar.close > ema:
                    signal_direction = "long"
                    confidence = min(0.8, abs(gap_bps) / 100.0) # Scale confidence with gap size
                    reason = "bullish_gap_down_reversion_above_ema"

                if signal_direction:
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "gap_bps": gap_bps,
                            "last_day_close": self.last_day_close,
                            "current_open": current_open,
                            "current_close": ctx.bar.close,
                            "ema_period": self.ema_period,
                            "ema_value": ema
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_today flag if the position was closed for a new day's trading
        # This might need refinement based on exact daily reset logic, but for now,
        # it's primarily handled by the 00:00 UTC check in on_bar.
        pass