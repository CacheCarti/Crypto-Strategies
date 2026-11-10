from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFadeSOL(Strategy):
    METADATA = {
        "name": "AsiaSessionFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Aim for 2-hour hold max, but session close is hard stop
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0 # UTC 00:00
        self.session_end_hour = 8   # UTC 08:00
        self.lookback_period = 20   # For session midpoint and range
        self.cooldown_bars = 6      # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.entry_price_this_session = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_close = None
        self.session_bars = []

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset session data at the start of a new session
        if current_hour == self.session_start_hour and (self.entry_price_this_session is not None or len(self.session_bars) > 0):
            # New session, reset everything
            self.entry_price_this_session = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_close = None
            self.session_bars = []

        # Update session data
        if self.session_start_hour <= current_hour < self.session_end_hour:
            if self.session_start_close is None:
                self.session_start_close = ctx.bar.open # Use open of the first bar as session start close
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bars.append(ctx.bar.close) # Track closes for midpoint

        # Always exit before the session ends if still in position
        if ctx.has_position() and current_hour >= self.session_end_hour:
            return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Do not trade if already in a position
        if ctx.has_position():
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider trades during the specified session
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Ensure we have enough session bars to calculate midpoint
        if len(self.session_bars) < 5: # Need at least a few bars to form a range
            return None

        # Calculate session midpoint and range
        session_midpoint = (max(self.session_bars) + min(self.session_bars)) / 2
        session_range = max(self.session_bars) - min(self.session_bars)

        # Conditions for fading
        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Avoid trading in crisis or strong trends
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
            return None

        # Calculate average true range for dynamic thresholds
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)
        atr_period = 14
        if len(highs) < atr_period + 1:
            return None

        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        atr = sum(trs[-atr_period:]) / atr_period if trs else 0.0

        if atr == 0:
            return None

        # Define 'stretched' based on current price deviation from session midpoint relative to ATR
        stretch_threshold_atr = 1.0 # Price is 1.0 ATR away from midpoint

        signal_confidence = 0.6 # Base confidence

        # Long signal: Price significantly below session midpoint, attempting to revert
        if current_price < session_midpoint - stretch_threshold_atr * atr:
            # Add a condition for "stalling" - e.g., current bar close is higher than previous close
            # but still far from midpoint. Or price is recovering.
            if ctx.bar.close > ctx.closes(2)[0]: # Current close is higher than previous
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_fade_long",
                        "current_price": current_price,
                        "session_midpoint": session_midpoint,
                        "session_range": session_range,
                        "atr": atr,
                        "deviation_from_midpoint": current_price - session_midpoint,
                        "regime": regime,
                        "trend_regime": trend_regime,
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate
                    }
                )

        # Short signal: Price significantly above session midpoint, attempting to revert
        if current_price > session_midpoint + stretch_threshold_atr * atr:
            # Add a condition for "stalling" - e.g., current bar close is lower than previous close
            # but still far from midpoint. Or price is falling.
            if ctx.bar.close < ctx.closes(2)[0]: # Current close is lower than previous
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_session_fade_short",
                        "current_price": current_price,
                        "session_midpoint": session_midpoint,
                        "session_range": session_range,
                        "atr": atr,
                        "deviation_from_midpoint": current_price - session_midpoint,
                        "regime": regime,
                        "trend_regime": trend_regime,
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any session-specific tracking that might interfere with the next session's logic
        # For this strategy, a new session automatically resets the state, so explicit reset here is less critical.
        # However, we can use this for logging or adaptive sizing if needed.
        pass