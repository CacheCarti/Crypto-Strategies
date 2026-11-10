from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionFade(Strategy):
    METADATA = {
        "name": "BTC_SessionFade_Overnight",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"] # Using these for filters
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 6    # UTC 06:00
        self.fade_trigger_hour = 5   # Fade around 05:00 UTC
        self.exit_before_hour = 9    # Exit before US session (13:00 UTC, 9 AM EST)
        self.min_session_range_pct = 0.5 # Minimum session range in % to consider fading
        self.max_session_range_pct = 3.0 # Max session range, avoid fading extreme moves
        self.cooldown_bars = 6       # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        self.ema_period = 20 # For trend filtering
        self.atr_period = 14 # For volatility filter and dynamic stop/take
        self.max_atr_threshold = 1.5 # Max ATR multiplier for session range
        self.min_atr_threshold = 0.5 # Min ATR multiplier for session range

        self.daily_session_high = -1.0
        self.daily_session_low = -1.0
        self.daily_session_open = -1.0
        self.session_midpoint = -1.0
        self.trade_today = False

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Reset session data at the start of a new session
        if current_hour == self.session_start_hour and ctx.bar.timestamp.minute == 0:
            self.daily_session_high = ctx.bar.high
            self.daily_session_low = ctx.bar.low
            self.daily_session_open = ctx.bar.open
            self.session_midpoint = (ctx.bar.high + ctx.bar.low) / 2.0
            self.trade_today = False
        elif current_hour > self.session_start_hour and current_hour < self.session_end_hour:
            # Update session high/low
            self.daily_session_high = max(self.daily_session_high, ctx.bar.high)
            self.daily_session_low = min(self.daily_session_low, ctx.bar.low)
            self.session_midpoint = (self.daily_session_high + self.daily_session_low) / 2.0

        # --- Exit logic ---
        if ctx.has_position():
            # Exit if past exit hour or if current session is over
            if current_hour >= self.exit_before_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "time_based_exit_pre_us_session",
                        "current_hour": current_hour,
                        "exit_hour": self.exit_before_hour
                    }
                )
            # Or if price crosses back to session midpoint (aggressive profit taking / stop)
            if ctx.position_direction() == "long" and current_price <= self.session_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_fade_reversion_to_midpoint_exit",
                        "current_price": current_price,
                        "session_midpoint": self.session_midpoint
                    }
                )
            if ctx.position_direction() == "short" and current_price >= self.session_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_fade_reversion_to_midpoint_exit",
                        "current_price": current_price,
                        "session_midpoint": self.session_midpoint
                    }
                )
            return None # Hold position if no exit condition met

        # --- Entry logic ---
        if self.trade_today: # Only one trade per session
            return None
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars: # Cooldown
            return None

        # Only consider trades during the fade trigger hour
        if current_hour != self.fade_trigger_hour:
            return None

        # Ensure session data is valid
        if self.daily_session_high <= 0 or self.daily_session_low <= 0 or self.daily_session_open <= 0:
            return None

        session_range = self.daily_session_high - self.daily_session_low
        session_range_pct = (session_range / self.daily_session_open) * 100.0

        # Filters
        if session_range_pct < self.min_session_range_pct or session_range_pct > self.max_session_range_pct:
            return None # Session not stretched enough or too extreme

        # Trend filter (EMA)
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Volatility filter (ATR)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None

        # Do not fade during crisis or strong trends
        if ctx.market.get("regime") in ["CRISIS", "MELTDOWN"]:
            return None
        if ctx.market.get("trend_regime_confidence", 0.0) > 0.6: # Avoid strong trends
            return None

        # Sentiment filter (Fear & Greed)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 20 or fear_greed > 80: # Avoid extreme F&G
            return None

        # Funding rate filter (avoid fading against strong perp sentiment)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        if funding_rate > 0.0002 or funding_rate < -0.0002: # High positive funding suggests long bias, high negative suggests short bias
            return None

        # Session Stretch Check (relative to ATR)
        # We want to fade when the price is near session high/low and session range is above average
        # And the current move is stretched.
        atr_factor = atr / current_price * 100 # ATR as a percentage of price
        if session_range_pct < atr_factor * self.min_atr_threshold or session_range_pct > atr_factor * self.max_atr_threshold:
            return None

        signal = None
        confidence = 0.65

        # Fade long (price is near session high, go short)
        if current_price > self.session_midpoint and \
           current_price > self.daily_session_high * 0.995 and \
           current_price > ema and \
           (current_price - self.session_midpoint) > (session_range * 0.4): # Stretched past midpoint
            signal = "short"
            self.trade_today = True
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": "overnight_session_fade_high",
                    "current_price": current_price,
                    "session_high": self.daily_session_high,
                    "session_midpoint": self.session_midpoint,
                    "session_range_pct": session_range_pct,
                    "ema": ema,
                    "atr_factor": atr_factor
                }
            )

        # Fade short (price is near session low, go long)
        if current_price < self.session_midpoint and \
           current_price < self.daily_session_low * 1.005 and \
           current_price < ema and \
           (self.session_midpoint - current_price) > (session_range * 0.4): # Stretched past midpoint
            signal = "long"
            self.trade_today = True
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                metadata={
                    "reason": "overnight_session_fade_low",
                    "current_price": current_price,
                    "session_low": self.daily_session_low,
                    "session_midpoint": self.session_midpoint,
                    "session_range_pct": session_range_pct,
                    "ema": ema,
                    "atr_factor": atr_factor
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, just logging if desired
        pass