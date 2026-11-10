from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionFade(Strategy):
    METADATA = {
        "name": "EuropeanSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-hour session holds
        "warmup_bars": 50,              # Need enough bars for 20-period indicators
        "required_features": [],        # No specific features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7 (8 AM CET)
        self.session_end_hour = 12   # UTC 12 (1 PM CET) - target exit by this hour
        self.entry_window_start = 9  # UTC 9
        self.entry_window_end = 11   # UTC 11
        self.lookback_period = 20    # For volatility and trend assessment
        self.cooldown_bars = 5       # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

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
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit existing positions near session end or if reversal fails
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            
            # Exit by session end
            if current_hour >= self.session_end_hour:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_time", "current_hour": current_hour})

            # Structure stop based on session range
            session_high = max(highs[self.session_start_hour - current_hour:]) if current_hour >= self.session_start_hour else ctx.bar.high
            session_low = min(lows[self.session_start_hour - current_hour:]) if current_hour >= self.session_start_hour else ctx.bar.low

            if pos_direction == "short":
                # If price moves significantly above session high after entry, stop out
                if current_price > entry_price * (1 + self.METADATA["declared_sl_bps"] / 10000.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_stop_loss_hit", "current_price": current_price, "entry_price": entry_price})
            elif pos_direction == "long":
                # If price moves significantly below session low after entry, stop out
                if current_price < entry_price * (1 - self.METADATA["declared_sl_bps"] / 10000.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_stop_loss_hit", "current_price": current_price, "entry_price": entry_price})
            
            return None # Hold position if no exit condition met

        # Only consider entries during the entry window
        if not (self.entry_window_start <= current_hour < self.entry_window_end):
            return None

        # Calculate session range and midpoint from session start
        session_prices = closes[-(current_hour - self.session_start_hour + 1):] if current_hour >= self.session_start_hour else [ctx.bar.close]
        
        if len(session_prices) < 2: # Need at least 2 bars to define a range
            return None

        session_open = session_prices[0]
        session_high = max(session_prices)
        session_low = min(session_prices)
        session_midpoint = (session_high + session_low) / 2

        # Regime filters
        # Do not trade in crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Avoid fading strong confirmed trends
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        if trend_confidence > 0.6 and trend_regime in ["bull", "bear"]: # If strong trend, stand down
            return None

        # Volatility filter: only fade if volatility is not excessively high
        stddev_closes = self._stddev(closes, self.lookback_period)
        if stddev_closes is None or stddev_closes == 0:
            return None
        
        # Calculate current deviation from session midpoint
        deviation_from_midpoint = (current_price - session_midpoint) / stddev_closes

        # Entry logic: Fade stretched moves towards the session midpoint
        signal_confidence = 0.0
        signal_direction = "hold"
        reason = ""

        # Fade upward stretch
        if deviation_from_midpoint > 1.5 and current_price > session_midpoint:
            signal_direction = "short"
            signal_confidence = min(0.7, abs(deviation_from_midpoint) / 3.0) # Scale confidence
            reason = "session_fade_upward_stretch"
        # Fade downward stretch
        elif deviation_from_midpoint < -1.5 and current_price < session_midpoint:
            signal_direction = "long"
            signal_confidence = min(0.7, abs(deviation_from_midpoint) / 3.0) # Scale confidence
            reason = "session_fade_downward_stretch"

        if signal_direction != "hold" and signal_confidence > 0.4:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "session_midpoint": session_midpoint,
                    "deviation_from_midpoint": deviation_from_midpoint,
                    "session_open": session_open,
                    "session_high": session_high,
                    "session_low": session_low,
                    "regime": ctx.market.get("regime", "NORMAL"),
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset adaptive state
        pass