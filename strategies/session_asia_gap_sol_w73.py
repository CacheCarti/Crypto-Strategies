from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0  # UTC 00:00
        self.asia_session_end_hour = 8    # UTC 08:00
        self.last_signal_bar_index = -100 # Cooldown
        self.min_cooldown_bars = 5       # Min bars to wait after a signal

        self.prior_session_close = None
        self.current_session_open = None
        self.session_traded_today = False # Track if we've traded in the current Asia session
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.min_gap_bps = 50 # Minimum gap size in basis points to consider

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for new Asia session
        if current_hour == self.asia_session_start_hour and ctx.bar_index > 0 and ctx.bar.timestamp.day != ctx.closes(2)[0].day:
            # New day, reset session_traded_today
            self.session_traded_today = False
            # Store prior day's close (yesterday's last bar close)
            # This is tricky with 1h bars. We need the close of the bar ending at UTC 23:00 yesterday.
            # ctx.closes(2)[0] is the close of the bar *before* the current one.
            # If current bar is 00:00, then ctx.closes(2)[0] is 23:00 yesterday.
            self.prior_session_close = ctx.closes(2)[0] if len(ctx.closes(2)) > 1 else None
            self.current_session_open = ctx.bar.open
            
        # Exit logic for positions held into end of Asia session
        if ctx.has_position() and current_hour == self.asia_session_end_hour - 1: # Exit one hour before session close
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "bar_index": ctx.bar_index})

        # Entry logic only within Asia session and if not traded today
        if self.asia_session_start_hour <= current_hour < self.asia_session_end_hour and not self.session_traded_today:
            if ctx.bar_index - self.last_signal_bar_index < self.min_cooldown_bars:
                return None # Cooldown in effect

            if self.prior_session_close is None or self.current_session_open is None:
                return None

            gap = self.current_session_open - self.prior_session_close
            gap_bps = (gap / self.prior_session_close) * 10000

            if abs(gap_bps) < self.min_gap_bps:
                return None # Gap too small

            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)

            if ema_short is None or ema_long is None:
                return None

            signal_direction = None
            confidence = 0.0
            reason = ""
            
            # Long entry: price gapped down, then shows signs of recovery/reversion
            if gap_bps < -self.min_gap_bps: # Significant gap down
                # If current price is moving back up towards the prior close, and short EMA crosses above long EMA
                if ctx.bar.close > self.current_session_open and ema_short > ema_long and closes[-2] < closes[-1]:
                    signal_direction = "long"
                    confidence = min(0.8, abs(gap_bps) / 200.0) # Confidence scales with gap size
                    reason = "asia_gap_down_reversion_long"

            # Short entry: price gapped up, then shows signs of decline/reversion
            elif gap_bps > self.min_gap_bps: # Significant gap up
                # If current price is moving back down towards the prior close, and short EMA crosses below long EMA
                if ctx.bar.close < self.current_session_open and ema_short < ema_long and closes[-2] > closes[-1]:
                    signal_direction = "short"
                    confidence = min(0.8, abs(gap_bps) / 200.0) # Confidence scales with gap size
                    reason = "asia_gap_up_reversion_short"
            
            if signal_direction and not ctx.has_position():
                self.last_signal_bar_index = ctx.bar_index
                self.session_traded_today = True # Mark as traded for the session
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "prior_session_close": self.prior_session_close,
                        "current_session_open": self.current_session_open,
                        "gap_bps": gap_bps,
                        "current_close": ctx.bar.close,
                        "ema_short": ema_short,
                        "ema_long": ema_long
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Reset session_traded_today if position closed early, allowing another trade
        # For this strategy, we want only one trade per session, so we keep session_traded_today as True.
        pass