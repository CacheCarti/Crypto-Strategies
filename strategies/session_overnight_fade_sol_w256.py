from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "SOL Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"], # For trend confirmation
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 6    # UTC 06:00
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.entry_cooldown_bars * 2 # Initialize far in past

        # Periods for session analysis and trend filtering
        self.sma_period_long = 48 # 2 days for trend
        self.sma_period_short = 12 # 12 hours for session strength
        self.vol_sma_period = 24 # 1 day for volume average

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
        closes = ctx.closes(self.sma_period_long + 1)
        opens = ctx.opens(self.sma_period_long + 1)
        highs = ctx.highs(self.sma_period_long + 1)
        lows = ctx.lows(self.sma_period_long + 1)
        volumes = ctx.volumes(self.vol_sma_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day # To ensure only one trade per day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.entry_cooldown_bars:
            return None

        # Trend filter: Avoid trading against strong long-term trend
        long_sma = self._sma(closes, self.sma_period_long)
        if long_sma is None:
            return None

        # Regime filter: Avoid crisis
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Session logic: Identify the overnight session (UTC 0-6)
        is_session_active = self.session_start_hour <= current_hour < self.session_end_hour
        
        # We want to enter *during* the session, but not right at the start.
        # Let's target the middle to end of the session for a fade.
        if not (self.session_start_hour + 2 <= current_hour < self.session_end_hour):
            return None

        # Check if we've already traded for this day
        if hasattr(self, 'last_trade_day') and self.last_trade_day == current_day:
            return None
        
        # Calculate session range and midpoint
        session_bars = []
        session_low = float('inf')
        session_high = float('-inf')
        session_open_price = None

        # Look back to find bars within the current session
        for i in range(len(closes) - 1, -1, -1):
            bar_time = ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            bar_time_i = ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0) # Simplified, assume same day
            
            # This is a simplification. A more robust way would involve iterating through `ctx.timestamps()`
            # and checking if the hour falls within the session for each bar.
            # For this strategy, we'll assume the most recent bars define the current session.
            # We need at least self.sma_period_short bars for session analysis
            if len(closes) - i <= self.sma_period_short:
                session_bars.append((opens[i], highs[i], lows[i], closes[i]))
                session_low = min(session_low, lows[i])
                session_high = max(session_high, highs[i])
                if session_open_price is None:
                    session_open_price = opens[i] # Open of the first bar of the session window considered
            else:
                break
        
        if not session_bars or session_open_price is None or session_high == session_low:
            return None

        session_midpoint = (session_high + session_low) / 2.0
        session_range = session_high - session_low
        
        if session_range == 0:
            return None

        # Calculate session strength (deviation from session open)
        current_session_move = ctx.bar.close - session_open_price
        
        # Volatility filter for session stretch (using standard deviation of recent closes)
        volatility = self._stddev(closes, self.sma_period_short)
        if volatility is None or volatility == 0:
            return None

        # Determine if the session move is "stretched" (e.g., beyond a certain multiple of volatility)
        stretch_threshold = 1.5 * volatility # 1.5 standard deviations from the open
        
        # Volume confirmation: current volume above average
        avg_volume = self._sma(volumes, self.vol_sma_period)
        if avg_volume is None:
            return None
        volume_confirm = ctx.bar.volume > avg_volume * 1.2 # 20% above average

        # BTC return as a general market trend filter
        btc_return = ctx.features.get("btc_return_pct", 0.0)

        # Entry Conditions
        signal = None
        confidence = 0.6 # Base confidence

        # Long entry: Session has moved significantly down, stretched, and is potentially reversing towards midpoint
        if current_session_move < -stretch_threshold and ctx.bar.close < session_midpoint:
            # Only go long if not in a strong downtrend (price above long SMA or slightly below)
            if ctx.bar.close > long_sma * 0.995 and btc_return > -0.5: # Not too bearish BTC
                if not ctx.has_position() or ctx.position_direction() == "short":
                    signal = ctx.signal(
                        "long",
                        confidence=confidence,
                        metadata={
                            "reason": "session_down_stretch_fade_to_midpoint",
                            "session_move": round(current_session_move, 2),
                            "stretch_threshold": round(stretch_threshold, 2),
                            "session_midpoint": round(session_midpoint, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "long_sma": round(long_sma, 2),
                            "btc_return_pct": round(btc_return, 2),
                            "volume_confirm": volume_confirm
                        }
                    )
        
        # Short entry: Session has moved significantly up, stretched, and is potentially reversing towards midpoint
        elif current_session_move > stretch_threshold and ctx.bar.close > session_midpoint:
            # Only go short if not in a strong uptrend (price below long SMA or slightly above)
            if ctx.bar.close < long_sma * 1.005 and btc_return < 0.5: # Not too bullish BTC
                if not ctx.has_position() or ctx.position_direction() == "long":
                    signal = ctx.signal(
                        "short",
                        confidence=confidence,
                        metadata={
                            "reason": "session_up_stretch_fade_to_midpoint",
                            "session_move": round(current_session_move, 2),
                            "stretch_threshold": round(stretch_threshold, 2),
                            "session_midpoint": round(session_midpoint, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "long_sma": round(long_sma, 2),
                            "btc_return_pct": round(btc_return, 2),
                            "volume_confirm": volume_confirm
                        }
                    )
        
        # Exit logic: If we are in a position and it's approaching the end of the session or next major session
        if ctx.has_position():
            # Exit before next US session (e.g., 13 UTC start, exit by 12 UTC)
            if current_hour >= 12: # Exit before US session opens
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_pre_us_session",
                        "current_hour": current_hour,
                        "position_direction": ctx.position_direction(),
                        "entry_price": round(ctx.position["entry_price"], 2),
                        "current_close": round(ctx.bar.close, 2)
                    }
                )
            
            # If price crosses midpoint against us, consider exiting or reducing confidence
            if ctx.position_direction() == "long" and ctx.bar.close > session_midpoint * 1.005: # Moved past midpoint
                return ctx.signal(
                    "flat",
                    confidence=0.4,
                    metadata={
                        "reason": "session_fade_target_exceeded_long",
                        "current_close": round(ctx.bar.close, 2),
                        "session_midpoint": round(session_midpoint, 2),
                        "position_direction": ctx.position_direction()
                    }
                )
            elif ctx.position_direction() == "short" and ctx.bar.close < session_midpoint * 0.995: # Moved past midpoint
                return ctx.signal(
                    "flat",
                    confidence=0.4,
                    metadata={
                        "reason": "session_fade_target_exceeded_short",
                        "current_close": round(ctx.bar.close, 2),
                        "session_midpoint": round(session_midpoint, 2),
                        "position_direction": ctx.position_direction()
                    }
                )

        if signal:
            self.last_trade_bar_index = ctx.bar_index
            self.last_trade_day = current_day
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for debugging or adaptive logic
        pass