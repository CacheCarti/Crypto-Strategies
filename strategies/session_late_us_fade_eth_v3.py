from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeETH(Strategy):
    METADATA = {
        "name": "Session Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18  # UTC 18:00
        self.session_end_hour = 23    # UTC 23:00 (last bar of session)
        self.midpoint_period = 20     # For calculating session midpoint
        self.stretch_threshold = 0.005 # Price move % from open to current to consider "stretched"
        self.fade_reversal_bars = 3   # Lookback for reversal confirmation
        self.min_confidence = 0.6
        self.cooldown_bars = 6        # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow first trade immediately

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_bar_index = ctx.bar_index

        # Check for cooldown
        if current_bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Do not trade in crisis or strong trend regimes
        if market_regime in ["CRISIS", "MELTDOWN"] or (abs(eth_return_pct) > 0.03 and trend_regime != "neutral"):
            return None

        # --- Session Logic ---
        # Only consider trading within the late US session
        if not (self.session_start_hour <= current_hour <= self.session_end_hour):
            # If it's after the session, and we have a position, close it to reset for next day
            if ctx.has_position() and current_hour == (self.session_end_hour + 1) % 24:
                self.last_trade_bar = current_bar_index # Update cooldown for next day
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_close_time", "current_hour": current_hour})
            return None

        # Find session open price (first bar of the session)
        session_opens = []
        for i in range(len(opens) - 1, -1, -1):
            bar_ts = ctx.bar.timestamp.replace(hour=current_hour - (len(opens) - 1 - i), minute=0, second=0, microsecond=0) # Approximate timestamp for historical bar
            if bar_ts.hour == self.session_start_hour:
                session_opens.append(opens[i])
            elif bar_ts.hour > self.session_start_hour:
                session_opens.append(opens[i])
            else:
                break # Before session start

        if not session_opens:
            return None # Not enough session data yet

        session_first_open = session_opens[0]
        session_current_price = closes[-1]
        session_move = (session_current_price - session_first_open) / session_first_open

        # Calculate session midpoint (SMA of closes during the session so far)
        session_closes = []
        for i in range(len(closes) - 1, -1, -1):
            bar_ts = ctx.bar.timestamp.replace(hour=current_hour - (len(closes) - 1 - i), minute=0, second=0, microsecond=0)
            if self.session_start_hour <= bar_ts.hour <= current_hour:
                session_closes.append(closes[i])
            elif bar_ts.hour < self.session_start_hour:
                break
        
        session_midpoint = self._sma(session_closes, len(session_closes)) if session_closes else None

        if session_midpoint is None:
            return None

        # Is the session stretched?
        is_stretched_up = session_move > self.stretch_threshold
        is_stretched_down = session_move < -self.stretch_threshold

        # Has price reversed from the stretched direction? (e.g., for stretched up, we need a reversal down)
        reversed_down = False
        reversed_up = False
        if len(closes) >= self.fade_reversal_bars:
            if is_stretched_up and closes[-1] < closes[-self.fade_reversal_bars]: # Current price lower than N bars ago
                reversed_down = True
            if is_stretched_down and closes[-1] > closes[-self.fade_reversal_bars]: # Current price higher than N bars ago
                reversed_up = True

        # Fade logic
        signal_direction: Optional[str] = None
        confidence = self.min_confidence
        reason = ""
        
        # Short signal: session stretched up, price reverses down, and we are above midpoint
        if not ctx.has_position() and is_stretched_up and reversed_down and closes[-1] > session_midpoint:
            signal_direction = "short"
            reason = "session_fade_short_stretched_up_reversal"
            confidence = min(1.0, self.min_confidence + (session_move - self.stretch_threshold) * 10) # Higher stretch, higher confidence

        # Long signal: session stretched down, price reverses up, and we are below midpoint
        elif not ctx.has_position() and is_stretched_down and reversed_up and closes[-1] < session_midpoint:
            signal_direction = "long"
            reason = "session_fade_long_stretched_down_reversal"
            confidence = min(1.0, self.min_confidence + (-session_move - self.stretch_threshold) * 10) # Higher stretch, higher confidence

        # Exit signal if we've reached the midpoint or moved against the fade
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "short" and closes[-1] <= session_midpoint: # Short target hit
                self.last_trade_bar = current_bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_fade_short_target_hit", "midpoint": session_midpoint, "current_price": closes[-1]})
            elif pos_dir == "long" and closes[-1] >= session_midpoint: # Long target hit
                self.last_trade_bar = current_bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_fade_long_target_hit", "midpoint": session_midpoint, "current_price": closes[-1]})
            # Or if it starts trending again strongly against us
            if pos_dir == "short" and eth_return_pct > 0.015: # Strong move up after short
                 self.last_trade_bar = current_bar_index
                 return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_fade_short_trend_reversal_exit", "eth_return_pct": eth_return_pct})
            if pos_dir == "long" and eth_return_pct < -0.015: # Strong move down after long
                 self.last_trade_bar = current_bar_index
                 return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_fade_long_trend_reversal_exit", "eth_return_pct": eth_return_pct})


        if signal_direction:
            self.last_trade_bar = current_bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_first_open": session_first_open,
                    "session_current_price": session_current_price,
                    "session_move_pct": session_move * 100,
                    "session_midpoint": session_midpoint,
                    "fear_greed_index": fear_greed,
                    "funding_rate_ethusdt": funding_rate,
                    "eth_return_pct": eth_return_pct
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position close results
        pass