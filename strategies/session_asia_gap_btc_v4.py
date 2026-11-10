from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaGapReversion(Strategy):
    METADATA = {
        "name": "BTC Asia Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended to hold for up to 4 hours within Asia session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_session_close = None
        self.traded_today = False
        self.cooldown_bars = 0
        self.cooldown_period = 5 # bars

        # Parameters for regime filtering
        self.volatility_period = 20
        self.min_volatility_zscore = -0.5 # Avoid extremely low volatility
        self.max_volatility_zscore = 1.5  # Avoid extremely high volatility

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # Reset traded_today flag at the start of a new UTC day (after Asia session ends)
        if current_hour == 9 and self.traded_today: # Reset after Asia session (0-8 UTC)
            self.traded_today = False
            self.last_session_close = None # Also reset prior session close

        # Calculate regime filters
        volatility_zscore = self._zscore([c - ctx.closes(2)[-2] for c in closes], self.volatility_period) # Volatility as 1-bar price change stddev
        
        # Trend filter using market context
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Asia session: UTC 0 to 8
        is_asia_session = 0 <= current_hour <= 8

        # If a position is open, check if it's time to exit at the end of Asia session
        if ctx.has_position():
            if current_hour == 8: # Exit at the end of Asia session
                self.cooldown_bars = self.cooldown_period
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position during session

        # Logic for the start of Asia session (UTC 0) to capture prior session close
        if current_hour == 0 and not self.traded_today:
            # The close of the bar at hour 23 UTC is the prior session's close.
            # We need at least two bars to compare current open with prior close.
            if len(ctx.closes(2)) >= 2:
                self.last_session_close = ctx.closes(2)[-2] # Close of the previous bar (UTC 23)
            else:
                self.last_session_close = None
            return None # Don't trade on the first bar of the session, just capture data

        # Entry logic within Asia session (UTC 1 to 8)
        if is_asia_session and not self.traded_today and self.last_session_close is not None:
            current_open = ctx.bar.open
            gap_threshold_bps = 50.0 # 0.5% gap
            gap_size_bps = (current_open - self.last_session_close) / self.last_session_close * 10000.0

            # Regime filters
            if not (self.min_volatility_zscore <= volatility_zscore <= self.max_volatility_zscore):
                return None # Stand down in extreme volatility
            if trend_regime != "neutral" or not (30 <= fear_greed_index <= 70):
                return None # Stand down if strong trend or extreme fear/greed

            # Check for a significant gap that we expect to revert
            if abs(gap_size_bps) > gap_threshold_bps:
                confidence = 0.6 + min(abs(gap_size_bps) / 200.0, 0.3) # Scale confidence with gap size

                if gap_size_bps > gap_threshold_bps: # Large positive gap, expect reversion down
                    if current_open > self.last_session_close: # Only if price is still above prior close
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_period
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            metadata={
                                "reason": "asia_session_gap_reversion_short",
                                "gap_size_bps": gap_size_bps,
                                "current_open": current_open,
                                "last_session_close": self.last_session_close,
                                "volatility_zscore": volatility_zscore,
                                "trend_regime": trend_regime,
                                "fear_greed_index": fear_greed_index,
                            }
                        )
                elif gap_size_bps < -gap_threshold_bps: # Large negative gap, expect reversion up
                    if current_open < self.last_session_close: # Only if price is still below prior close
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_period
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            metadata={
                                "reason": "asia_session_gap_reversion_long",
                                "gap_size_bps": gap_size_bps,
                                "current_open": current_open,
                                "last_session_close": self.last_session_close,
                                "volatility_zscore": volatility_zscore,
                                "trend_regime": trend_regime,
                                "fear_greed_index": fear_greed_index,
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # For logging or adaptive sizing. Not used for signaling.
        pass