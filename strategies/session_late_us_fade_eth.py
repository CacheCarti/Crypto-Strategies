from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthLateUSSessionFade(Strategy):
    METADATA = {
        "name": "EthLateUSSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold until session end or TP/SL
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"], # Using fear_greed_index as a macro filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18  # UTC 18:00 (late US session start)
        self.session_end_hour = 23   # UTC 23:00 (last bar for entry)
        self.exit_hour = 0           # UTC 00:00 (exit before this hour)
        self.lookback_period = 6     # Lookback for session range (18:00 to current bar)
        self.cooldown_bars = 12      # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars * 2 # Initialize to allow first trade

        # For regime filtering
        self.ema_fast_period = 20
        self.ema_slow_period = 50

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # Regime filter: Avoid trading in strong trends or crisis
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        # Check for strong trend (EMAs spread out and consistent direction)
        trend_threshold_pct = 0.005 # 0.5% spread
        if abs(ema_fast - ema_slow) / current_price > trend_threshold_pct:
            if (ema_fast > ema_slow and closes[-1] > ema_fast) or \
               (ema_fast < ema_slow and closes[-1] < ema_fast):
                # Strong trend, avoid fading
                return None

        # Crisis regime filter
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Fear & Greed filter: Avoid fading during extreme greed/fear
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed > 75 or fear_greed < 25: # Extreme Greed or Extreme Fear
            return None

        # Exit existing position if outside session or at exit hour
        if ctx.has_position():
            if current_hour >= self.exit_hour and current_hour < self.session_start_hour:
                # Close position before new session or at exit hour
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

            # Check for intra-position structure stop (e.g., price crosses back session midpoint)
            # This requires storing session info on entry, let's keep it simple for now and rely on SL/TP/time

        # Entry logic: Only within the late US session
        if not (self.session_start_hour <= current_hour <= self.session_end_hour):
            return None
        
        # Calculate session range and midpoint
        session_closes = []
        session_highs = []
        session_lows = []
        
        # Collect data from the beginning of the session up to current bar
        # Assuming bars are hourly, look back up to `lookback_period` bars from current session start
        # to capture the session's range.
        
        # Find the start of the current session within the available bars
        session_start_bar_index = -1
        for i in range(len(ctx.closes(self.lookback_period + 1)) -1, -1, -1):
            bar_time = ctx.bar.timestamp.replace(hour=current_hour - (len(ctx.closes(self.lookback_period+1)) - 1 - i), minute=0, second=0, microsecond=0) # Crude time reconstruction
            # A more robust way would be to iterate ctx.history if available, or rely on precise bar timestamps
            # For simplicity, we'll assume the last `lookback_period` bars are within the session if current_hour is in session.
            # This is a simplification. A perfect implementation would track actual session start bar.
            
            # For this simplified version, let's just take the last 'lookback_period' bars within the session.
            # This means the "session range" is dynamic, not fixed from session start.
            
            # If current_hour is 19, and lookback is 6, we consider 19, 18, 17, 16, 15, 14, 13.
            # We want bars from this session, i.e., from 18:00 onwards.
            # So, if current_hour is 19, and session_start_hour is 18, we need 2 bars.
            
            # Let's collect bars from the current hour back to session_start_hour
            num_bars_into_session = current_hour - self.session_start_hour + 1
            if num_bars_into_session < 2: # Need at least 2 bars to define a range
                return None

            session_closes = ctx.closes(num_bars_into_session)[-num_bars_into_session:]
            session_highs = ctx.highs(num_bars_into_session)[-num_bars_into_session:]
            session_lows = ctx.lows(num_bars_into_session)[-num_bars_into_session:]

            if len(session_closes) < 2:
                return None
            
            session_high = max(session_highs)
            session_low = min(session_lows)
            session_midpoint = (session_high + session_low) / 2.0

            # Check for "stretched" condition
            # Price significantly above/below midpoint, near session high/low
            stretch_threshold_factor = 0.6 # Price must be in top/bottom 40% of the session range
            
            range_size = session_high - session_low
            if range_size == 0:
                return None

            # Long condition: Price is near session low, but stretched downwards
            is_stretched_low = (current_price < session_low + range_size * (1 - stretch_threshold_factor)) and \
                               (current_price < session_midpoint) # Price in bottom 40% AND below midpoint

            # Short condition: Price is near session high, but stretched upwards
            is_stretched_high = (current_price > session_high - range_size * (1 - stretch_threshold_factor)) and \
                                (current_price > session_midpoint) # Price in top 40% AND above midpoint

            # Entry confirmation: Current close must be reversing slightly
            # E.g., for a short, current close must be lower than previous but still high
            # For simplicity, let's use the stretch and the midpoint fade idea.
            
            # Fade the stretch towards midpoint
            if not ctx.has_position():
                if is_stretched_high and current_price > session_midpoint:
                    # Stretched high, fading towards midpoint (short)
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=0.8,
                                      metadata={"reason": "late_us_session_fade_stretched_high",
                                                "current_hour": current_hour,
                                                "session_high": session_high,
                                                "session_low": session_low,
                                                "session_midpoint": session_midpoint,
                                                "current_price": current_price,
                                                "fear_greed": fear_greed})
                elif is_stretched_low and current_price < session_midpoint:
                    # Stretched low, fading towards midpoint (long)
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=0.8,
                                      metadata={"reason": "late_us_session_fade_stretched_low",
                                                "current_hour": current_hour,
                                                "session_high": session_high,
                                                "session_low": session_low,
                                                "session_midpoint": session_midpoint,
                                                "current_price": current_price,
                                                "fear_greed": fear_greed})
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log or adapt if needed. Cooldown is handled in on_bar.
        pass