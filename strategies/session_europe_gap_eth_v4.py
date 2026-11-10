from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeSessionGapReversion(Strategy):
    METADATA = {
        "name": "EuropeSessionGapReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1 hour, but allow up to end of session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 7  # UTC 7:00
        self.session_close_hour = 12 # UTC 12:00
        self.min_gap_bps = 50.0  # Minimum gap size in bps to consider
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.last_session_close_price = None
        self.last_bar_day = None
        self.trade_taken_today = False

        # Parameters for regime filter
        self.trend_period = 20
        self.volatility_period = 20
        self.min_fear_greed_neutral = 40
        self.max_fear_greed_neutral = 60

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day
        current_close = ctx.bar.close

        # Daily reset for trade_taken_today
        if self.last_bar_day is not None and current_day != self.last_bar_day:
            self.trade_taken_today = False
        self.last_bar_day = current_day

        # Update last_session_close_price at the end of the session
        if current_hour == self.session_close_hour - 1 and self.last_session_close_price is None:
            # This is the bar *before* the close hour, so we can capture the close of the session
            # For 1-hour bars, if session_close_hour is 12, we want the 11:00 bar's close.
            self.last_session_close_price = current_close

        # Reset last_session_close_price after the session closes
        if current_hour == self.session_close_hour:
            self.last_session_close_price = None # Reset for next session

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime Gating
        # Price-based trend filter (SMA direction)
        sma_slow = self._sma(closes, self.trend_period)
        if sma_slow is None or len(closes) < self.trend_period + 1:
            return None
        
        # Volatility filter (low volatility is better for reversion)
        std_dev = self._stddev(closes, self.volatility_period)
        if std_dev is None:
            return None
        
        # Simple volatility check: if std_dev is too high, stand down
        # (adjust threshold based on domain, here 1-hour ETH)
        avg_price = self._sma(closes, self.volatility_period)
        if avg_price is None: return None
        volatility_percentage = (std_dev / avg_price) * 100
        
        # Fear & Greed filter
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Stand down if market is too volatile or sentiment is extreme
        if volatility_percentage > 1.0 or \
           fear_greed_index < self.min_fear_greed_neutral or \
           fear_greed_index > self.max_fear_greed_neutral:
            return None # Skip trading in extreme conditions

        # Entry logic: Only during the European morning session and if no trade taken today
        if current_hour >= self.session_open_hour and \
           current_hour < self.session_close_hour and \
           not self.trade_taken_today and \
           self.last_session_close_price is not None and \
           not ctx.has_position():

            gap_bps = ((current_close - self.last_session_close_price) / self.last_session_close_price) * 10000

            if abs(gap_bps) >= self.min_gap_bps:
                signal_direction = None
                reason = ""
                confidence = 0.0

                if gap_bps > self.min_gap_bps:  # Large upward gap, look for short reversion
                    signal_direction = "short"
                    reason = "europe_session_gap_short_reversion"
                    confidence = min(1.0, abs(gap_bps) / 200.0) # Scale confidence with gap size
                elif gap_bps < -self.min_gap_bps: # Large downward gap, look for long reversion
                    signal_direction = "long"
                    reason = "europe_session_gap_long_reversion"
                    confidence = min(1.0, abs(gap_bps) / 200.0) # Scale confidence with gap size

                if signal_direction:
                    self.last_trade_bar = ctx.bar_index
                    self.trade_taken_today = True # Mark that a trade has been taken
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": reason,
                            "gap_bps": gap_bps,
                            "last_session_close": self.last_session_close_price,
                            "current_close": current_close,
                            "regime_volatility_pct": volatility_percentage,
                            "regime_fear_greed": fear_greed_index,
                        }
                    )

        # Exit logic: if in position and session is closing
        if ctx.has_position() and current_hour >= self.session_close_hour:
            self.last_trade_bar = ctx.bar_index # Cooldown after exit
            return ctx.signal(
                "flat",
                confidence=0.5, # Always exit at end of session
                metadata={
                    "reason": "session_close_exit",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                    "regime_volatility_pct": volatility_percentage,
                    "regime_fear_greed": fear_greed_index,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on trade outcome
        # For this strategy, we'll keep it simple and just log.
        ctx.log(f"Position closed. Direction: {position['direction']}, Return: {position['return_bps']} bps, Bars held: {position['bars_held']}")
        # self.last_trade_bar = ctx.bar_index # Ensure cooldown even if not explicitly handled in on_bar