from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuMorningGapReversion(Strategy):
    METADATA = {
        "name": "BTC EU Morning Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-6 hours
        "warmup_bars": 24, # Need a full prior day's close
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar_index = -100 # Cooldown period
        self.prior_session_close = None
        self.prior_session_open = None
        self.traded_today = False
        self.eu_session_start_hour = 7 # UTC
        self.eu_session_end_hour = 12 # UTC

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset traded_today flag at the start of a new day (UTC 00:00)
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False

        # Capture prior session's closing price
        # We define the "prior session" as the end of the previous EU session (hour 12 UTC)
        # So, we want the close of the bar at UTC 11 (which is the close of the 11-12 UTC hour bar)
        if current_hour == self.eu_session_end_hour - 1 and ctx.bar.timestamp.minute == 59:
            self.prior_session_close = ctx.bar.close
            self.prior_session_open = None # Reset for next session's open

        # Detect the start of the EU session (7 AM UTC)
        if current_hour == self.eu_session_start_hour and self.prior_session_open is None:
            # This is the first bar of the EU session (7-8 AM UTC bar)
            self.prior_session_open = ctx.bar.open

        # --- Entry Logic ---
        if not ctx.has_position() and not self.traded_today and \
           ctx.bar_index > self.last_trade_bar_index + 12: # Cooldown of 12 bars (12 hours)

            # Trade only within the first few hours of the EU session
            if self.eu_session_start_hour <= current_hour < self.eu_session_start_hour + 3: # Trade 7-9 AM UTC
                if self.prior_session_close is not None and self.prior_session_open is not None:
                    # Calculate gap size from prior EU session close to current EU session open
                    gap_pct = ((self.prior_session_open - self.prior_session_close) / self.prior_session_close) * 100

                    # Look for a significant gap that is not continuing further in the same direction
                    # and price is showing signs of mean reversion towards the prior close.
                    
                    # Simple moving average for trend filter
                    short_ema_period = 10
                    long_ema_period = 20
                    short_ema = self._ema(closes, short_ema_period)
                    long_ema = self._ema(closes, long_ema_period)

                    if short_ema is None or long_ema is None:
                        return None

                    # Check for large gap down and current price is above session open, trying to fill the gap
                    if gap_pct < -0.5: # Significant gap down (e.g., -0.5% or more)
                        if ctx.bar.close > self.prior_session_open and short_ema > long_ema: # Price trying to recover, bullish momentum
                            self.last_trade_bar_index = ctx.bar_index
                            self.traded_today = True
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                metadata={
                                    "reason": "eu_morning_gap_fill_long",
                                    "gap_pct": round(gap_pct, 2),
                                    "prior_close": round(self.prior_session_close, 2),
                                    "session_open": round(self.prior_session_open, 2),
                                    "current_close": round(ctx.bar.close, 2),
                                    "short_ema": round(short_ema, 2),
                                    "long_ema": round(long_ema, 2),
                                }
                            )
                    # Check for large gap up and current price is below session open, trying to fill the gap
                    elif gap_pct > 0.5: # Significant gap up (e.g., +0.5% or more)
                        if ctx.bar.close < self.prior_session_open and short_ema < long_ema: # Price trying to revert, bearish momentum
                            self.last_trade_bar_index = ctx.bar_index
                            self.traded_today = True
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                metadata={
                                    "reason": "eu_morning_gap_fill_short",
                                    "gap_pct": round(gap_pct, 2),
                                    "prior_close": round(self.prior_session_close, 2),
                                    "session_open": round(self.prior_session_open, 2),
                                    "current_close": round(ctx.bar.close, 2),
                                    "short_ema": round(short_ema, 2),
                                    "long_ema": round(long_ema, 2),
                                }
                            )

        # --- Exit Logic ---
        if ctx.has_position():
            # Exit by the end of the EU session (12 PM UTC) if position is still open
            if current_hour >= self.eu_session_end_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "eu_session_end_time_exit",
                        "current_hour": current_hour,
                        "position_direction": ctx.position_direction(),
                        "entry_price": round(ctx.position["entry_price"], 2),
                        "current_close": round(ctx.bar.close, 2),
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.last_trade_bar_index = ctx.bar_index

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema