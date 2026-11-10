from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFadeETH(Strategy):
    METADATA = {
        "name": "AsiaSessionFadeETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended max hold until end of session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0
        self.session_end_hour = 8
        self.lookback_period = 20 # For volatility/range check
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Store session data
        self.current_session_day = -1
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_bars = []
        self.session_midpoint = None
        self.traded_this_session = False

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
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Session management
        if current_day != self.current_session_day:
            # New day, reset session data
            self.current_session_day = current_day
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_bars = []
            self.session_midpoint = None
            self.traded_this_session = False

        # Update session data if within session hours
        if self.session_start_hour <= current_hour < self.session_end_hour:
            if self.session_open_price is None:
                self.session_open_price = ctx.bar.open
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bars.append(ctx.bar.close)
            self.session_midpoint = (self.session_high + self.session_low) / 2.0
        else:
            # Outside session hours, ensure no active session data from previous day
            if self.session_open_price is not None: # Session just ended
                self.session_open_price = None # Clear for next session

        # Exit logic: Close position before session end or if trend is strong
        if ctx.has_position():
            # Exit if session is about to end
            if current_hour >= self.session_end_hour - 1: # Exit in the last hour of session
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Exit if market regime becomes crisis
            if ctx.market.get("regime") == "CRISIS":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit"})

        # Entry logic: Only within Asia session, only if not traded yet, and cooldown respected
        if not ctx.has_position() and \
           self.session_start_hour <= current_hour < self.session_end_hour - 1 and \
           not self.traded_this_session and \
           ctx.bar_index > self.last_trade_bar + self.cooldown_bars and \
           self.session_open_price is not None and \
           len(self.session_bars) > 5: # Ensure some session history has accumulated

            # Filters
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 20 or fear_greed > 80: # Avoid extreme sentiment
                return None

            if ctx.market.get("regime") in ["CRISIS", "MELTDOWN", "HIGH_VOL"]: # Avoid crisis/high vol
                return None

            # Calculate session range and current extension
            session_range = self.session_high - self.session_low
            current_extension_up = ctx.bar.close - self.session_high
            current_extension_down = self.session_low - ctx.bar.close

            # Volatility filter: only fade if current range is significantly larger than recent average
            if len(closes) < self.lookback_period:
                return None
            recent_ranges = [ctx.highs(self.lookback_period)[i] - ctx.lows(self.lookback_period)[i] for i in range(self.lookback_period)]
            avg_recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0
            
            # Require session range to be at least 1.5x average recent range to be "stretched"
            if session_range < 1.5 * avg_recent_range:
                return None

            # Fade conditions
            signal_direction = None
            signal_confidence = 0.0

            # Long entry: price is significantly below session open and low, indicating a stretch down
            # and current close has recovered slightly from the low, aiming for midpoint
            if ctx.bar.close < self.session_low * 0.995 and ctx.bar.close > self.session_low * 1.005 and \
               ctx.bar.close < self.session_midpoint * 0.99: # Must be below midpoint to fade up
                signal_direction = "long"
                signal_confidence = 0.7
                reason = "asia_session_fade_long_oversold"

            # Short entry: price is significantly above session open and high, indicating a stretch up
            # and current close has pulled back slightly from the high, aiming for midpoint
            elif ctx.bar.close > self.session_high * 1.005 and ctx.bar.close < self.session_high * 0.995 and \
                 ctx.bar.close > self.session_midpoint * 1.01: # Must be above midpoint to fade down
                signal_direction = "short"
                signal_confidence = 0.7
                reason = "asia_session_fade_short_overbought"
            
            if signal_direction:
                self.traded_this_session = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": reason,
                        "session_open": self.session_open_price,
                        "session_high": self.session_high,
                        "session_low": self.session_low,
                        "session_midpoint": self.session_midpoint,
                        "current_close": ctx.bar.close,
                        "session_range_mult_avg": session_range / avg_recent_range
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position outcome
        pass