from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class BtcEuropeDrift(Strategy):
    METADATA = {
        "name": "BTC Europe Drift",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7 # UTC 7:00
        self.entry_hour_end = 12  # UTC 12:00
        self.exit_hour_latest = 12 # UTC 12:00
        self.min_session_move_factor = 1.2 # Must exceed 1.2x median range
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        self.atr_period = 14
        self.range_period = 20 # For median range calculation

        # Parameters for regime gating
        self.min_fear_greed_for_long = 40 # Avoid extreme fear for long
        self.max_fear_greed_for_short = 60 # Avoid extreme greed for short
        self.min_volatility_multiplier = 0.5 # Only trade if current volatility is at least 0.5x average
        self.max_volatility_multiplier = 2.0 # Avoid trading in excessively volatile conditions

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate session open and close for the current day
        # Look back to find the 7:00 UTC bar for the current day
        session_open_price = None
        session_high = ctx.bar.high
        session_low = ctx.bar.low
        session_volume = ctx.bar.volume
        
        # We need at least the last 6 bars to potentially capture the session start
        if len(ctx.closes(self.entry_hour_start + 1)) < self.entry_hour_start + 1:
            return None

        # Find the starting bar of the European session for today
        # Iterate backwards from current bar to find the 7 AM bar
        
        # This is a simplified approach, assuming we are always within a day.
        # For precise session tracking across midnight, we'd need more complex state.
        # For this strategy, we look back within the current day's bars only.
        
        # Check if we are within the European session trading window
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            # If we are past the exit time and have a position, flat it
            if ctx.has_position() and current_hour >= self.exit_hour_latest:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_exit_time", "current_hour": current_hour})
            return None # Not in entry window, not exiting, so do nothing

        # Find the session's opening price (7:00 UTC bar close)
        # We need to ensure we have enough history to find the 7 AM bar of the current day
        # This is a critical part, needs careful indexing
        
        # Let's assume the bar at ctx.bar_index is current_hour.
        # We need to find the bar corresponding to self.entry_hour_start.
        # The number of bars back to the start of the session (7 AM)
        bars_since_session_start = current_hour - self.entry_hour_start
        
        if bars_since_session_start < 0 or bars_since_session_start >= len(ctx.opens(bars_since_session_start + 1)):
            return None # Not enough history or hour mismatch

        session_open_price = ctx.opens(bars_since_session_start + 1)[0] # Open of the 7:00 UTC bar
        
        # Calculate session high/low/volume up to the current bar
        session_period_closes = ctx.closes(bars_since_session_start + 1)
        session_period_highs = ctx.highs(bars_since_session_start + 1)
        session_period_lows = ctx.lows(bars_since_session_start + 1)
        session_period_volumes = ctx.volumes(bars_since_session_start + 1)

        if not session_period_closes:
            return None

        session_high = max(session_period_highs)
        session_low = min(session_period_lows)
        session_volume = sum(session_period_volumes)

        # Skip if single bar spike (not a sustained move)
        if bars_since_session_start == 0: # Only the 7 AM bar, not enough for "move"
            return None

        # Calculate the move from session open
        current_price = ctx.bar.close
        session_move = current_price - session_open_price
        session_move_abs = abs(session_move)

        # Calculate median true range for volatility filter
        median_range = self._median_range(highs, lows, self.range_period)
        if median_range is None or median_range == 0:
            return None
        
        # Check if the session move is decisive
        if session_move_abs < median_range * self.min_session_move_factor:
            return None # Not a decisive move

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Volatility filter using ATR
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        avg_atr = self._atr(highs[:-bars_since_session_start] if bars_since_session_start > 0 else highs, 
                            lows[:-bars_since_session_start] if bars_since_session_start > 0 else lows, 
                            closes[:-bars_since_session_start] if bars_since_session_start > 0 else closes, 
                            self.atr_period)
        
        if current_atr is None or avg_atr is None or avg_atr == 0:
            return None

        volatility_ratio = current_atr / avg_atr

        if not (self.min_volatility_multiplier <= volatility_ratio <= self.max_volatility_multiplier):
            return None # Volatility not in desired range

        # Trend and Fear/Greed based gates
        is_bullish_regime = (trend_regime == "bull" or fear_greed_index > self.min_fear_greed_for_long)
        is_bearish_regime = (trend_regime == "bear" or fear_greed_index < self.max_fear_greed_for_short)

        signal_direction = None
        reason = ""
        confidence = 0.0

        if not ctx.has_position():
            # Long condition: price has moved significantly up from session open
            if session_move > 0 and current_price > session_open_price and is_bullish_regime:
                signal_direction = "long"
                reason = "session_europe_drift_long"
                confidence = 0.7 + min(0.3, (session_move_abs / median_range - self.min_session_move_factor) / 2) # Scale confidence by strength of move

            # Short condition: price has moved significantly down from session open
            elif session_move < 0 and current_price < session_open_price and is_bearish_regime:
                signal_direction = "short"
                reason = "session_europe_drift_short"
                confidence = 0.7 + min(0.3, (session_move_abs / median_range - self.min_session_move_factor) / 2) # Scale confidence by strength of move
        
        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "entry_hour_start": self.entry_hour_start,
                    "entry_hour_end": self.entry_hour_end,
                    "current_hour": current_hour,
                    "session_open_price": session_open_price,
                    "current_price": current_price,
                    "session_move": session_move,
                    "median_range": median_range,
                    "fear_greed_index": fear_greed_index,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime,
                    "volatility_ratio": volatility_ratio,
                }
            )

        # Exit logic based on stop structure (opposite move) or time
        if ctx.has_position():
            pos = ctx.position
            # Stop structure: if price retraces significantly against the session move
            if pos["direction"] == "long" and ctx.bar.close < pos["entry_price"] - (current_atr * 0.5): # 0.5 ATR stop
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "entry_price": pos["entry_price"], "current_price": ctx.bar.close, "current_atr": current_atr})
            elif pos["direction"] == "short" and ctx.bar.close > pos["entry_price"] + (current_atr * 0.5): # 0.5 ATR stop
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "entry_price": pos["entry_price"], "current_price": ctx.bar.close, "current_atr": current_atr})
            
            # Time-based exit already handled at the top
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive sizing or logging on close
        pass