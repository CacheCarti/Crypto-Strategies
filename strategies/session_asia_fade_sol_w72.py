from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "Asia Session Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 3600, # Max hold time, but aiming for session close
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.lookback_period = 20    # For session range calculation
        self.ema_period = 20         # For trend filter
        self.rsi_period = 14         # For overbought/oversold confirmation
        self.max_stretch_factor = 0.015 # Percentage move to consider stretched (1.5%)
        self.min_stretch_factor = 0.005 # Minimum percentage move for a fade (0.5%)
        self.last_trade_bar = -100   # Cooldown for entries
        self.cooldown_bars = 6
        self.daily_traded = False

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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day_of_year = ctx.bar.timestamp.timetuple().tm_yday

        # Reset daily_traded flag at the start of a new day
        if current_day_of_year != getattr(self, '_last_day_of_year', -1):
            self.daily_traded = False
            self._last_day_of_year = current_day_of_year

        # Exit logic first: If we have a position and it's near session end
        if ctx.has_position():
            # Exit if approaching session end or if it's a new session and we are still holding
            if current_hour >= self.session_end_hour - 1 or current_hour < self.session_start_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_near_session_end",
                    "current_hour": current_hour,
                    "session_end_hour": self.session_end_hour
                })
            # Exit if market regime becomes crisis, regardless of session
            if ctx.market.get("regime") == "CRISIS":
                 return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "exit_crisis_regime",
                    "market_regime": ctx.market.get("regime")
                })
            return None # Hold position if conditions not met for exit

        # Entry logic: Only trade within the Asia session, once per day, and after cooldown
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        if self.daily_traded or (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Gather data for session analysis
        session_closes = ctx.closes(self.lookback_period)
        session_highs = ctx.highs(self.lookback_period)
        session_lows = ctx.lows(self.lookback_period)
        
        if len(session_closes) < self.lookback_period:
            return None

        # Calculate session range and midpoint
        session_high = max(session_highs)
        session_low = min(session_lows)
        session_mid = (session_high + session_low) / 2.0
        session_range = session_high - session_low

        # Calculate current stretch from session midpoint
        current_price = ctx.bar.close
        stretch_from_mid = (current_price - session_mid) / session_mid

        # Trend filter (EMA)
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # RSI confirmation
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Avoid trading in crisis or strong trends
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        if ctx.market.get("trend_regime_confidence", 0.0) > 0.6 and trend_regime in ["bull", "bear"]:
             return None # Avoid strong confirmed trends

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade long if overstretched to the upside and stalling
        if stretch_from_mid > self.max_stretch_factor and current_price > ema and rsi > 70:
            # Check for stalling: current close below open, or small candle body
            if ctx.bar.close < ctx.bar.open or abs(ctx.bar.close - ctx.bar.open) < (session_range * 0.05):
                signal_direction = "short"
                confidence = 0.6 + min(0.3, (stretch_from_mid - self.max_stretch_factor) * 50) # Scale confidence by stretch
                reason = "asia_session_fade_short_stretched"
        
        # Fade short if overstretched to the downside and stalling
        elif stretch_from_mid < -self.max_stretch_factor and current_price < ema and rsi < 30:
            # Check for stalling: current close above open, or small candle body
            if ctx.bar.close > ctx.bar.open or abs(ctx.bar.close - ctx.bar.open) < (session_range * 0.05):
                signal_direction = "long"
                confidence = 0.6 + min(0.3, (-stretch_from_mid - self.max_stretch_factor) * 50) # Scale confidence by stretch
                reason = "asia_session_fade_long_stretched"
        
        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.daily_traded = True # Only one trade per day
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "session_midpoint": session_mid,
                    "stretch_factor": stretch_from_mid,
                    "rsi": rsi,
                    "ema": ema,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed,
                    "funding_rate": funding_rate
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive sizing or logging based on position performance
        pass