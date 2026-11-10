from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFade(Strategy):
    METADATA = {
        "name": "USSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13  # UTC 13:00
        self.session_close_hour = 21 # UTC 21:00 (exit before this)
        self.lookback_period = 20 # For session range and midpoint
        self.ema_period = 20 # For trend filtering
        self.rsi_period = 14 # For overbought/oversold confirmation
        self.cooldown_bars = 5
        self.last_signal_bar_index = -self.cooldown_bars

        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_close_prev = None # Close of the previous bar in the session
        self.session_midpoint = None
        self.session_started = False
        self.trade_today = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        # Then apply EMA formula
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1) # Need one more for RSI diff
        highs = ctx.highs(self.lookback_period)
        lows = ctx.lows(self.lookback_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session data at the start of a new day (after session close)
        if current_hour == self.session_close_hour + 1 and self.session_started:
            self.session_started = False
            self.trade_today = False
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = None
            self.session_close_prev = None
            self.session_midpoint = None

        # --- Session Tracking ---
        if current_hour == self.session_open_hour and not self.session_started:
            self.session_started = True
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_close_prev = ctx.bar.close
            self.session_midpoint = (self.session_open + ctx.bar.close) / 2 # Initial midpoint
            return None # Don't trade on the first bar of the session

        if self.session_started and current_hour >= self.session_open_hour and current_hour < self.session_close_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_midpoint = (self.session_open + ctx.bar.close) / 2 # Midpoint updates with current close
            self.session_close_prev = ctx.bar.close

        # --- Exit Logic (End of Session) ---
        if ctx.has_position() and current_hour >= self.session_close_hour and ctx.position_direction() != "flat":
            reason = "session_end_exit"
            if ctx.position_direction() == "long" and ctx.bar.close < ctx.position["entry_price"]:
                reason = "session_end_exit_loss"
            elif ctx.position_direction() == "long" and ctx.bar.close > ctx.position["entry_price"]:
                reason = "session_end_exit_profit"
            elif ctx.position_direction() == "short" and ctx.bar.close > ctx.position["entry_price"]:
                reason = "session_end_exit_loss"
            elif ctx.position_direction() == "short" and ctx.bar.close < ctx.position["entry_price"]:
                reason = "session_end_exit_profit"

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_price": ctx.bar.close, "session_midpoint": self.session_midpoint})

        # --- Entry Logic ---
        if not self.session_started or self.trade_today:
            return None # Only trade during US session and only once per day

        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None # Cooldown active

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        
        if ema is None or rsi is None:
            return None

        # Trend filter (avoid fading strong trends)
        # Use ctx.market.get("trend_regime") as a more robust trend filter
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime in ["bull", "bear"]: # Avoid fading strong trends
            return None

        # Regime filter (avoid crisis)
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Fear & Greed filter (avoid extreme sentiment)
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")
        if fear_greed_class in ["Extreme Fear", "Extreme Greed"]:
            return None

        # Session range check
        if self.session_open is None or self.session_midpoint is None:
            return None

        session_range = self.session_high - self.session_low
        if session_range == 0: # Avoid division by zero
            return None

        # Calculate current position in the session range
        current_pos_in_range = (ctx.bar.close - self.session_low) / session_range
        
        # Determine if the session move is "stretched"
        # Stretched long: current close is in the top 10-20% of the session range AND above midpoint
        # Stretched short: current close is in the bottom 10-20% of the session range AND below midpoint
        stretched_threshold = 0.85 # Top 15% for long fade, bottom 15% for short fade
        
        signal = None
        confidence = 0.0
        reason = ""

        # Fade Long (price stretched up, fade back to midpoint)
        if (ctx.bar.close > self.session_midpoint and
            current_pos_in_range > stretched_threshold and
            rsi > 65 and # Overbought
            ctx.bar.close > ema # Price above EMA (confirming upward stretch)
        ):
            # Check for reversal confirmation (e.g., current bar close lower than previous bar in session)
            if self.session_close_prev is not None and ctx.bar.close < self.session_close_prev:
                signal = "short"
                confidence = 0.75
                reason = "us_session_fade_long_stretch_rsi_overbought"

        # Fade Short (price stretched down, fade back to midpoint)
        elif (ctx.bar.close < self.session_midpoint and
              current_pos_in_range < (1 - stretched_threshold) and
              rsi < 35 and # Oversold
              ctx.bar.close < ema # Price below EMA (confirming downward stretch)
        ):
            # Check for reversal confirmation (e.g., current bar close higher than previous bar in session)
            if self.session_close_prev is not None and ctx.bar.close > self.session_close_prev:
                signal = "long"
                confidence = 0.75
                reason = "us_session_fade_short_stretch_rsi_oversold"

        if signal:
            self.last_signal_bar_index = ctx.bar_index
            self.trade_today = True # Only one trade per session
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": ctx.bar.close,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": self.session_midpoint,
                    "rsi": rsi,
                    "ema": ema,
                    "current_pos_in_range": current_pos_in_range,
                    "trend_regime": trend_regime,
                    "fear_greed_class": fear_greed_class,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass