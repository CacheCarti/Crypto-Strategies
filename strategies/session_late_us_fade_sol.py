from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionFade(Strategy):
    METADATA = {
        "name": "SOL US Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Intended hold for 3 hours, max until session end
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.session_start_hour = 18 # UTC 18:00
        self.session_end_hour = 23 # UTC 23:00 (trade until 23:59)
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_volume = 0
        self.session_bars_count = 0
        self.session_active = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period)
        highs = ctx.highs(self.long_ema_period)
        lows = ctx.lows(self.long_ema_period)
        volumes = ctx.volumes(self.long_ema_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Session management
        if current_hour == self.session_start_hour and not self.session_active:
            # New session starts
            self.session_active = True
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_volume = ctx.bar.volume
            self.session_bars_count = 1
        elif self.session_active and current_hour >= self.session_start_hour and current_hour <= self.session_end_hour:
            # Continue session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_volume += ctx.bar.volume
            self.session_bars_count += 1
        elif self.session_active and current_hour > self.session_end_hour:
            # Session ends, reset
            self.session_active = False
            self.session_open = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_volume = 0
            self.session_bars_count = 0

        # Exit any existing position before the session ends (UTC 00:00)
        if ctx.has_position() and current_hour == 0 and ctx.position_direction() is not None:
            return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        long_ema = self._ema(closes, self.long_ema_period)
        short_ema = self._ema(closes, self.short_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if long_ema is None or short_ema is None or rsi is None:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Do not trade in crisis or strong confirmed trends
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
            return None

        # Only trade during the defined session
        if not self.session_active or self.session_bars_count < 3: # Need at least a few bars to form session context
            return None

        # Calculate session midpoint and range
        if self.session_open is None: # Should not happen if session_active is True, but for safety
             return None
        
        session_midpoint = (self.session_high + self.session_low) / 2
        session_range = self.session_high - self.session_low
        
        # Determine if price is unusually stretched from session open
        stretch_threshold_factor = 0.015 # 1.5% move from session open
        stretch_up = self.session_open * (1 + stretch_threshold_factor)
        stretch_down = self.session_open * (1 - stretch_threshold_factor)

        # Confidence based on volatility and funding rate
        confidence = 0.6
        if abs(funding_rate) > 0.0002: # High funding rate indicates strong directional bias, reducing fade confidence
            confidence *= 0.7
        if regime == "HIGH_VOL": # Reduce confidence in very volatile conditions
            confidence *= 0.8
        
        # Long Entry: Price is stretched down, RSI oversold, fading towards midpoint
        if not ctx.has_position() and current_close < stretch_down and rsi < 30 and current_close < short_ema and short_ema < long_ema:
            # Ensure price is moving away from the midpoint
            if current_close < session_midpoint:
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_session_fade_long",
                        "current_hour": current_hour,
                        "session_open": self.session_open,
                        "current_close": current_close,
                        "rsi": rsi,
                        "short_ema": short_ema,
                        "long_ema": long_ema,
                        "session_midpoint": session_midpoint,
                        "stretch_down_level": stretch_down,
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

        # Short Entry: Price is stretched up, RSI overbought, fading towards midpoint
        if not ctx.has_position() and current_close > stretch_up and rsi > 70 and current_close > short_ema and short_ema > long_ema:
            # Ensure price is moving away from the midpoint
            if current_close > session_midpoint:
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "sol_session_fade_short",
                        "current_hour": current_hour,
                        "session_open": self.session_open,
                        "current_close": current_close,
                        "rsi": rsi,
                        "short_ema": short_ema,
                        "long_ema": long_ema,
                        "session_midpoint": session_midpoint,
                        "stretch_up_level": stretch_up,
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # You could adapt confidence or parameters here based on trade outcome
        # For example: if position["return_bps"] < 0: self.cooldown_bars = 20