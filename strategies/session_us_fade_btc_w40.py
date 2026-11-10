from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "BTC US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours, but session-bound
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_hour = 13  # UTC 13:00
        self.session_close_hour = 21 # UTC 21:00 (exit before this)
        self.lookback_period = 20 # For session range and midpoint
        self.ema_period = 20 # For trend filter
        self.min_stretch_ratio = 1.5 # How much price stretched beyond avg range
        self.max_stretch_ratio = 3.0 # Upper bound to avoid fading extreme moves
        self.min_rsi_fade = 65 # RSI for short fade
        self.max_rsi_fade = 35 # RSI for long fade
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For daily state tracking
        self.traded_today = False
        self.current_day = -1 # Initialize with an invalid day

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

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Reset daily state
        if ctx.bar.timestamp.day != self.current_day:
            self.traded_today = False
            self.current_day = ctx.bar.timestamp.day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Check if within US session window
        if not (self.session_open_hour <= current_hour < self.session_close_hour):
            # If we have a position and it's time to close before session end
            if ctx.has_position() and current_hour == self.session_close_hour:
                self.traded_today = True # Mark as traded to prevent re-entry today
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_exit"})
            return None

        # Don't trade if already traded today
        if self.traded_today:
            return None

        # Calculate session range and midpoint from previous bars
        # For simplicity, let's use the last N bars before the session opens for range calculation
        # This is a proxy for "previous session activity"
        prev_closes = ctx.closes(self.lookback_period + self.warmup_bars)[-self.lookback_period-1:-1]
        prev_highs = ctx.highs(self.lookback_period + self.warmup_bars)[-self.lookback_period-1:-1]
        prev_lows = ctx.lows(self.lookback_period + self.warmup_bars)[-self.lookback_period-1:-1]

        if len(prev_closes) < self.lookback_period:
            return None

        prev_avg_range = sum([h - l for h, l in zip(prev_highs, prev_lows)]) / len(prev_highs)
        prev_midpoint = (max(prev_highs) + min(prev_lows)) / 2 # A simple proxy for "session midpoint"

        # Trend filter: Use EMA to avoid fading strong trends
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Market regime filter
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        # Avoid trading in crisis or extreme fear/greed
        if crisis_score > 0.5 or fear_greed_index < 20 or fear_greed_index > 80:
            return None

        # Calculate current session stretch
        current_session_high = max(highs[-self.lookback_period:])
        current_session_low = min(lows[-self.lookback_period:])
        current_session_range = current_session_high - current_session_low

        # Check for unusually stretched move
        stretch_ratio = current_session_range / prev_avg_range if prev_avg_range > 0 else 0

        # Only consider fading if stretch is significant but not extreme
        if not (self.min_stretch_ratio < stretch_ratio < self.max_stretch_ratio):
            return None

        # RSI filter
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""

        # Long entry (fade down move)
        if current_price < ema and current_price < prev_midpoint and rsi < self.max_rsi_fade:
            # Price is below EMA, below previous midpoint, and oversold
            # Ensure it's stretched to the downside
            if current_price < current_session_low + (current_session_range * (1 - stretch_ratio / self.max_stretch_ratio)):
                signal_direction = "long"
                confidence = max(0.5, min(0.9, (self.max_rsi_fade - rsi) / (self.max_rsi_fade - 10))) # Scale confidence by how oversold
                reason = "session_fade_long_oversold"

        # Short entry (fade up move)
        elif current_price > ema and current_price > prev_midpoint and rsi > self.min_rsi_fade:
            # Price is above EMA, above previous midpoint, and overbought
            # Ensure it's stretched to the upside
            if current_price > current_session_high - (current_session_range * (1 - stretch_ratio / self.max_stretch_ratio)):
                signal_direction = "short"
                confidence = max(0.5, min(0.9, (rsi - self.min_rsi_fade) / (100 - self.min_rsi_fade))) # Scale confidence by how overbought
                reason = "session_fade_short_overbought"

        if signal_direction and not ctx.has_position():
            self.traded_today = True
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "ema": ema,
                    "rsi": rsi,
                    "prev_midpoint": prev_midpoint,
                    "stretch_ratio": stretch_ratio,
                    "fear_greed": fear_greed_index
                }
            )
        elif ctx.has_position() and ctx.position_direction() != signal_direction and signal_direction is not None:
             # Flip position if a strong opposing signal comes
            self.traded_today = True # Consider this as a new trade for daily limit
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence * 1.1, # Higher confidence for a flip
                metadata={
                    "reason": f"flip_from_{ctx.position_direction()}_{reason}",
                    "current_price": current_price,
                    "ema": ema,
                    "rsi": rsi,
                    "prev_midpoint": prev_midpoint,
                    "stretch_ratio": stretch_ratio,
                    "fear_greed": fear_greed_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log for analysis, but no strategy changes here
        pass