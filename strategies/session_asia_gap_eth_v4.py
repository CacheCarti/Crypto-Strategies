from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaGapReversion(Strategy):
    METADATA = {
        "name": "EthAsiaGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50, # Need enough bars for 20-period SMA for trend filter
        "required_features": [], # No specific features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0  # UTC 00:00
        self.asia_session_end_hour = 8    # UTC 08:00
        self.min_gap_pct = 0.25           # Minimum gap percentage to consider (0.25%)
        self.reversion_threshold_pct = 0.15 # Price must move this much against gap for confirmation (0.15%)
        self.cooldown_bars = 4            # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # For trend filtering: 20-period SMA
        self.trend_period = 20
        self.trend_filter_enabled = True # Enable/disable trend filter

        # Store prior session's close for gap calculation
        self.prior_session_close = None
        self.prior_session_end_hour = 0 # Track when the last session ended to get its close

        # Track if we've traded in the current Asia session
        self.traded_this_session = False
        self.current_session_day = -1 # To reset traded_this_session daily

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Daily reset for `traded_this_session`
        if current_day != self.current_session_day:
            self.traded_this_session = False
            self.current_session_day = current_day

        # Update prior session close logic
        # Assuming prior session ends at UTC 00:00 for simplicity of daily cycle
        # If the current hour is 0 (start of new Asia session), the prior_session_close should be the close of the *previous day's* 23:00 bar.
        # However, for this strategy, we are interested in the gap from the *previous day's last bar* to *this session's first bar*.
        # Let's define the "prior session close" as the close of the bar just before the Asia session starts (i.e., the UTC 23:00 bar of the previous day).
        # We need to capture the last close *before* the Asia session starts.
        if current_hour == self.asia_session_start_hour:
            # If we just entered the Asia session, the prior_session_close should be the close of the last bar from the previous day.
            # ctx.closes(-2) would give us the 2nd to last close, which is the 23:00 bar of the prior day if current is 00:00.
            if len(closes) >= 2: # Ensure we have at least two bars
                self.prior_session_close = closes[-2]
            else:
                self.prior_session_close = None # Not enough history yet
            self.traded_this_session = False # Reset for new session

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Trend filter (SMA 20)
        sma20 = self._sma(closes, self.trend_period)
        if self.trend_filter_enabled and sma20 is None:
            return None

        # Check for position close at the end of the Asia session
        if ctx.has_position() and current_hour == self.asia_session_end_hour:
            # Close any open position at the end of the Asia session
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Entry logic within Asia session
        if self.asia_session_start_hour <= current_hour < self.asia_session_end_hour and not ctx.has_position() and not self.traded_this_session:
            if self.prior_session_close is None:
                return None

            open_price = ctx.bar.open
            current_close = ctx.bar.close

            gap_pct = ((open_price - self.prior_session_close) / self.prior_session_close) * 100

            # Long entry: large negative gap (price opened lower), then price moves up
            if gap_pct < -self.min_gap_pct: # Significant gap down
                reversion_pct = ((current_close - open_price) / open_price) * 100
                if reversion_pct > self.reversion_threshold_pct: # Price moved up from open
                    if self.trend_filter_enabled and current_close < sma20: # Only long in uptrend or neutral
                        return None
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "asia_session_gap_reversion_long",
                            "prior_session_close": self.prior_session_close,
                            "open_price": open_price,
                            "gap_pct": round(gap_pct, 2),
                            "reversion_pct": round(reversion_pct, 2),
                            "current_close": current_close,
                            "sma20": round(sma20, 2) if sma20 else "N/A"
                        }
                    )
            # Short entry: large positive gap (price opened higher), then price moves down
            elif gap_pct > self.min_gap_pct: # Significant gap up
                reversion_pct = ((open_price - current_close) / open_price) * 100
                if reversion_pct > self.reversion_threshold_pct: # Price moved down from open
                    if self.trend_filter_enabled and current_close > sma20: # Only short in downtrend or neutral
                        return None
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "asia_session_gap_reversion_short",
                            "prior_session_close": self.prior_session_close,
                            "open_price": open_price,
                            "gap_pct": round(gap_pct, 2),
                            "reversion_pct": round(reversion_pct, 2),
                            "current_close": current_close,
                            "sma20": round(sma20, 2) if sma20 else "N/A"
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        # print(f"Position closed: {position['direction']} at {ctx.bar.timestamp}. Return: {position['return_bps']} bps. Held for {position['bars_held']} bars.")
        pass