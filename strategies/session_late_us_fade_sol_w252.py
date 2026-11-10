from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionFade(Strategy):
    METADATA = {
        "name": "SOL Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended to hold for a few hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.session_length = 6 # hours
        self.entry_hour_start = 18 # UTC 18:00
        self.entry_hour_end = 23 # UTC 23:00 (last bar at 23:00)
        self.exit_hour = 0 # UTC 00:00

        self.last_trade_bar = -self.warmup_bars # Initialize to allow trade immediately after warmup
        self.cooldown_bars = 5 # Wait 5 bars after a trade

        # Store session open and midpoint for the current session
        self.session_open_price: Optional[float] = None
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.session_start_bar: Optional[int] = None
        self.session_midpoint: Optional[float] = None
        self.traded_this_session = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        # Apply EMA formula for subsequent values
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
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Check for end of session (UTC 00:00)
        if current_hour == self.exit_hour and self.session_open_price is not None:
            # Reset session data for the new day
            self.session_open_price = None
            self.session_high = None
            self.session_low = None
            self.session_start_bar = None
            self.session_midpoint = None
            self.traded_this_session = False
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close_exit", "current_hour": current_hour})

        # --- Session Tracking ---
        # If we are within the entry hours (18-23 UTC)
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            if self.session_open_price is None: # Start of a new session
                self.session_open_price = current_close
                self.session_high = current_close
                self.session_low = current_close
                self.session_start_bar = ctx.bar_index
                self.session_midpoint = current_close # Initial midpoint
            else: # Update session high/low
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
                self.session_midpoint = (self.session_high + self.session_low) / 2.0
        else: # Outside session hours, reset session tracking if it was active
            if self.session_open_price is not None and not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
                self.session_open_price = None
                self.session_high = None
                self.session_low = None
                self.session_start_bar = None
                self.session_midpoint = None
                self.traded_this_session = False


        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Do not trade if a position is already open, unless it's an explicit exit
        if ctx.has_position():
            # If current bar is past session end, close position
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_forced_exit", "current_hour": current_hour})
            return None

        # Do not trade if we already traded this session
        if self.traded_this_session:
            return None

        # --- Indicators ---
        short_ema = self._ema(closes, self.short_ema_period)
        long_ema = self._ema(closes, self.long_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if short_ema is None or long_ema is None or rsi is None or self.session_open_price is None:
            return None

        # --- Regime Filters ---
        fear_greed = ctx.features.get("fear_greed_index", 50)
        # Avoid trading during extreme fear/greed or crisis regimes
        if fear_greed < 25 or fear_greed > 75 or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None # Market too emotional or risky

        # Avoid fading strong trends (short EMA should be relatively close to long EMA)
        # And also avoid if current price is strongly trending away from the session midpoint
        trend_divergence = abs(short_ema - long_ema) / current_close * 10000 # bps
        if trend_divergence > 50: # If EMAs are divergent by more than 50 bps, trend is strong
            return None


        # --- Strategy Logic: Fade the session extreme towards midpoint ---
        signal_direction: Optional[str] = None
        reason = ""
        confidence = 0.0

        # Long Entry: Price is significantly below session midpoint and oversold, trying to revert
        if self.session_start_bar is not None and current_hour >= self.entry_hour_start + 2: # Wait at least 2 hours into the session
            if current_close < self.session_midpoint * (1 - 0.005) and rsi < 30: # 0.5% below midpoint and oversold
                if short_ema > long_ema: # Only long if overall trend is still bullish (or neutral)
                    signal_direction = "long"
                    confidence = 0.6 + (1 - rsi / 30) * 0.3 # Higher confidence for lower RSI
                    reason = "sol_session_fade_long_oversold_below_midpoint"

            # Short Entry: Price is significantly above session midpoint and overbought, trying to revert
            elif current_close > self.session_midpoint * (1 + 0.005) and rsi > 70: # 0.5% above midpoint and overbought
                if short_ema < long_ema: # Only short if overall trend is still bearish (or neutral)
                    signal_direction = "short"
                    confidence = 0.6 + ((rsi - 70) / 30) * 0.3 # Higher confidence for higher RSI
                    reason = "sol_session_fade_short_overbought_above_midpoint"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.traded_this_session = True # Only one trade per session
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open": self.session_open_price,
                    "session_midpoint": self.session_midpoint,
                    "current_close": current_close,
                    "rsi": rsi,
                    "short_ema": short_ema,
                    "long_ema": long_ema,
                    "fear_greed": fear_greed,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session if the position was closed for any reason
        # This allows a new trade in the same session if the conditions reset,
        # but the primary logic is to trade once per session.
        # For this strategy, we want to enforce traded_this_session = True
        # if a trade was initiated, to stick to the "once per session" rule.
        # So, we don't reset it here. The daily reset handles it.
        pass