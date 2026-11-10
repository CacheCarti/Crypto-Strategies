from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCOvernightFade(Strategy):
    METADATA = {
        "name": "BTC Overnight Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for up to 4 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 0-6 is the overnight session
        self.session_end_hour = 6
        self.fade_threshold_pct = 0.5  # Percentage move from session open to trigger fade
        self.fade_period = 6           # Lookback period for session move
        self.fade_sma_period = 20      # SMA for trend confirmation
        self.max_cooldown_bars = 12    # Cooldown after a trade
        self.cooldown_counter = 0      # Current cooldown counter
        self.last_signal_bar_index = -1
        self.last_session_day = -1
        self.last_signal_direction = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.fade_sma_period + self.fade_period)
        opens = ctx.opens(self.fade_sma_period + self.fade_period)
        highs = ctx.highs(self.fade_sma_period + self.fade_period)
        lows = ctx.lows(self.fade_sma_period + self.fade_period)

        if len(closes) < self.fade_sma_period + self.fade_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown logic
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            return None

        # Reset session state at the start of a new day for the session
        if current_day != self.last_session_day and current_hour == self.session_start_hour:
            self.last_session_day = current_day
            self.last_signal_direction = None # Allow a new signal for the new session

        # Only consider trading within the overnight session
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Ensure we only signal once per session
        if self.last_signal_bar_index == ctx.bar_index - 1 and self.last_signal_direction is not None:
             return None # Already signaled in the previous bar for this session

        # Calculate session move
        session_closes = closes[-(self.fade_period):]
        session_opens = opens[-(self.fade_period):]

        if len(session_closes) < self.fade_period or len(session_opens) < self.fade_period:
            return None

        session_open_price = session_opens[0]
        current_price = ctx.bar.close
        session_move_pct = ((current_price - session_open_price) / session_open_price) * 100

        # Trend filter (using a longer SMA)
        sma = self._sma(closes, self.fade_sma_period)
        if sma is None:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid trading in crisis or strong trend regimes
        if crisis_score > 0.6 or market_regime in ["CRISIS", "MELTDOWN"]:
            return None # Avoid high instability

        # Avoid fading strong trends (price well above/below SMA)
        if (current_price > sma * 1.01 and session_move_pct > 0) or \
           (current_price < sma * 0.99 and session_move_pct < 0):
            return None # Don't fade strong confirmed trends

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long signal (fade a downward move)
        if session_move_pct < -self.fade_threshold_pct and \
           current_price < sma: # Price below SMA but moving down too fast
            
            # Check if this is a transition from a less stretched state
            prev_session_move_pct = ((closes[-2] - session_open_price) / session_open_price) * 100 if len(closes) > 1 else 0
            if prev_session_move_pct >= -self.fade_threshold_pct: # Only trigger on the first bar crossing threshold
                signal_direction = "long"
                confidence = 0.6 + (100 - fear_greed_index) / 100 * 0.2 + twitter_sentiment * 0.1 # Higher confidence if more fear/bearish sentiment
                reason = "overnight_down_fade"
                self.cooldown_counter = self.max_cooldown_bars
                self.last_signal_bar_index = ctx.bar_index
                self.last_signal_direction = signal_direction


        # Short signal (fade an upward move)
        elif session_move_pct > self.fade_threshold_pct and \
             current_price > sma: # Price above SMA but moving up too fast

            # Check if this is a transition from a less stretched state
            prev_session_move_pct = ((closes[-2] - session_open_price) / session_open_price) * 100 if len(closes) > 1 else 0
            if prev_session_move_pct <= self.fade_threshold_pct: # Only trigger on the first bar crossing threshold
                signal_direction = "short"
                confidence = 0.6 + fear_greed_index / 100 * 0.2 + (-twitter_sentiment) * 0.1 # Higher confidence if more greed/bullish sentiment
                reason = "overnight_up_fade"
                self.cooldown_counter = self.max_cooldown_bars
                self.last_signal_bar_index = ctx.bar_index
                self.last_signal_direction = signal_direction

        if signal_direction and not ctx.has_position():
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "session_open_price": session_open_price,
                    "session_move_pct": session_move_pct,
                    "sma_fade_period": sma,
                    "fear_greed_index": fear_greed_index,
                    "twitter_sentiment": twitter_sentiment,
                    "market_regime": market_regime
                }
            )
        elif ctx.has_position() and ctx.position_direction() == "long" and signal_direction == "short":
            # Flip from long to short
            return ctx.signal(
                "short",
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flip_long_to_short_" + reason,
                    "current_price": current_price,
                    "session_open_price": session_open_price,
                    "session_move_pct": session_move_pct,
                    "sma_fade_period": sma,
                    "fear_greed_index": fear_greed_index,
                    "twitter_sentiment": twitter_sentiment,
                    "market_regime": market_regime
                }
            )
        elif ctx.has_position() and ctx.position_direction() == "short" and signal_direction == "long":
            # Flip from short to long
            return ctx.signal(
                "long",
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flip_short_to_long_" + reason,
                    "current_price": current_price,
                    "session_open_price": session_open_price,
                    "session_move_pct": session_move_pct,
                    "sma_fade_period": sma,
                    "fear_greed_index": fear_greed_index,
                    "twitter_sentiment": twitter_sentiment,
                    "market_regime": market_regime
                }
            )

        # Exit position if session is ending soon or if outside session hours (not strictly needed due to horizon_seconds but good for explicit exit)
        if ctx.has_position() and current_hour >= self.session_end_hour - 1: # Exit an hour before session end
            return ctx.signal(
                "flat",
                confidence=0.5, # Moderate confidence for time-based exit
                metadata={
                    "reason": "session_end_exit",
                    "current_price": current_price,
                    "session_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes
        self.cooldown_counter = self.max_cooldown_bars
        self.last_signal_direction = None # Allow new signal in next session
        ctx.log_debug(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held for {position['bars_held']} bars. Cooldown for {self.max_cooldown_bars} bars.")