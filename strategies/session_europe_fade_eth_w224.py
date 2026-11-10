from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuroOpenFade(Strategy):
    METADATA = {
        "name": "Euro Open Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold until session end
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7 AM
        self.session_end_hour = 12   # UTC 12 PM
        self.lookback_bars = 6      # Bars to check session move
        self.cooldown_bars = 12     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Thresholds for "stretched" move
        self.stretch_threshold_pct = 0.8 # 0.8% move in 6 hours is significant
        self.reversal_candle_pct = 0.2   # Current candle is small relative to session move
        self.min_session_range_pct = 0.5 # Only fade if session has moved enough

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 1) # Need current and previous bars
        opens = ctx.opens(self.lookback_bars + 1)
        highs = ctx.highs(self.lookback_bars + 1)
        lows = ctx.lows(self.lookback_bars + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Exit open position if past session end hour or if new session starts
        if ctx.has_position():
            if current_hour >= self.session_end_hour or current_hour < self.session_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "session_end": self.session_end_hour,
                        "close_price": ctx.bar.close
                    }
                )
            # Do not re-enter or flip during session if already in position
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entries during the European morning session
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # --- Regime Filters ---
        # Avoid trading in crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Avoid trading against strong trends (simple SMA filter)
        sma_50 = self._sma(closes, 50)
        if sma_50 is None:
            return None

        # --- Session Structure Analysis ---
        # Find session open price (first bar of the session)
        session_open_price = None
        session_high = -math.inf
        session_low = math.inf
        
        # Look back to find the start of the current session
        # This is a bit tricky as we only have current bar context.
        # We'll approximate by checking recent bars if they are within the session.
        # For simplicity, we'll use the open of the first bar within the session window as reference.
        # A more robust approach would require storing session start price in initialize or on_position_close.
        
        # Let's define the "session start" as the first bar we see within the session window (7-12 UTC)
        # after a period outside the session. This is a simplification.
        
        # For this strategy, we'll define the 'session open' as the open of the current bar,
        # and look at the move from that point to gauge stretch. This is simpler and less error-prone
        # than trying to find the exact session start bar from limited history.
        
        # Let's consider the move from the start of the current session hour up to now.
        # This is a bit of a hack but fits within constraints.
        
        # We need at least self.lookback_bars within the session
        session_bars_closes = []
        session_bars_highs = []
        session_bars_lows = []
        
        # Collect bars for the current session up to now
        # This assumes the current bar is inside the session (checked above)
        # and we look back up to `lookback_bars` within the session.
        # This is a simplification. A perfect implementation would need to track session state.
        
        # We'll use a simpler definition: the move over the last `lookback_bars`
        # if all those bars are within the session.
        
        # Check if we have enough bars *within the current session*
        # This is tricky with only `ctx.closes(n)`.
        # Simplification: use the last `lookback_bars` and assume they are part of the session's progression.
        # The key is to measure the move from an earlier point in the session to now.
        
        # Let's use the open of `lookback_bars` ago as the "session start" for this calculation.
        if len(closes) < self.lookback_bars + 1:
            return None

        # Session open for calculation purposes: open price from `lookback_bars` ago
        calc_session_open = opens[0] 
        current_price = ctx.bar.close

        session_move_abs = current_price - calc_session_open
        session_move_pct = (session_move_abs / calc_session_open) * 100.0

        # Calculate session range for context
        session_high_so_far = max(highs)
        session_low_so_far = min(lows)
        session_range_pct = ((session_high_so_far - session_low_so_far) / calc_session_open) * 100.0

        # Check for "stretched" move
        is_stretched_long = session_move_pct > self.stretch_threshold_pct
        is_stretched_short = session_move_pct < -self.stretch_threshold_pct

        # Check for "stalling" (small current candle relative to session range)
        current_candle_range = ctx.bar.high - ctx.bar.low
        is_stalling = current_candle_range < (session_high_so_far - session_low_so_far) * self.reversal_candle_pct
        
        # Ensure there's been enough movement in the session to fade
        has_min_range = session_range_pct > self.min_session_range_pct

        # --- Entry Logic ---
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Fade a stretched long move
        if is_stretched_long and is_stalling and has_min_range:
            # Check against trend: Don't short if price is significantly above SMA
            if current_price > sma_50 * 1.002: # 0.2% buffer above SMA
                return None
            
            # Check for high greed (might signal exhaustion)
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed > 65: # High greed
                signal_direction = "short"
                reason = "euro_open_fade_stretched_long_stalling_high_greed"
                confidence = 0.6 + (fear_greed - 65) / 35 * 0.2 # Scale confidence with greed

        # Fade a stretched short move
        elif is_stretched_short and is_stalling and has_min_range:
            # Check against trend: Don't long if price is significantly below SMA
            if current_price < sma_50 * 0.998: # 0.2% buffer below SMA
                return None

            # Check for high fear (might signal exhaustion)
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 35: # High fear
                signal_direction = "long"
                reason = "euro_open_fade_stretched_short_stalling_high_fear"
                confidence = 0.6 + (35 - fear_greed) / 35 * 0.2 # Scale confidence with fear
        
        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence), # Cap confidence at 1.0
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "session_move_pct": session_move_pct,
                    "session_range_pct": session_range_pct,
                    "current_candle_range": current_candle_range,
                    "stalling_factor": self.reversal_candle_pct,
                    "price": ctx.bar.close,
                    "sma_50": sma_50,
                    "fear_greed": ctx.features.get("fear_greed_index", 50)
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing for debugging/analysis
        pass