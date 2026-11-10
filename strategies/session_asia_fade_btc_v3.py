from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFadeBTC(Strategy):
    METADATA = {
        "name": "AsiaSessionFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold until end of session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_session_start_hour = 0
        self.asia_session_end_hour = 8 # UTC
        self.min_session_range_pct = 0.5 # Minimum range for a fade to be considered
        self.fade_threshold_pct = 0.7 # How far into the session range price needs to be to fade
        self.cooldown_bars = 6 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.traded_today = False
        self.session_high = -1.0
        self.session_low = -1.0
        self.session_open = -1.0
        self.session_mid = -1.0
        self.session_start_bar_index = -1

        # Indicators
        self.long_ema_period = 30
        self.short_ema_period = 10

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + 1)
        highs = ctx.highs(self.long_ema_period + 1)
        lows = ctx.lows(self.long_ema_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Reset session data at the start of a new Asia session
        if current_hour == self.asia_session_start_hour and (ctx.bar_index != self.session_start_bar_index):
            self.session_open = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.traded_today = False
            self.session_start_bar_index = ctx.bar_index
        elif current_hour >= self.asia_session_start_hour and current_hour < self.asia_session_end_hour:
            # Update session high/low
            if self.session_high == -1.0 or ctx.bar.high > self.session_high:
                self.session_high = ctx.bar.high
            if self.session_low == -1.0 or ctx.bar.low < self.session_low:
                self.session_low = ctx.bar.low
        else:
            # Outside Asia session, reset session tracking for next day
            self.session_high = -1.0
            self.session_low = -1.0
            self.session_open = -1.0
            self.session_mid = -1.0
            self.session_start_bar_index = -1
            self.traded_today = False # Ensure reset for next day

        # Exit existing position if outside session or near end
        if ctx.has_position():
            if current_hour >= self.asia_session_end_hour - 1: # Exit one hour before session end
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "session_end_hour": self.asia_session_end_hour
                })

        # Do not trade if already traded today or cooldown active
        if self.traded_today or (ctx.bar_index - self.last_trade_bar < self.cooldown_bars):
            return None

        # Only consider trades within the Asia session, after initial formation
        if current_hour >= self.asia_session_start_hour + 2 and current_hour < self.asia_session_end_hour - 1: # Avoid trading first two hours and last hour
            if self.session_high == -1.0 or self.session_low == -1.0 or self.session_open == -1.0:
                return None

            session_range = self.session_high - self.session_low
            if session_range == 0:
                return None

            session_mid = self.session_low + session_range / 2
            self.session_mid = session_mid

            # Minimum range for a valid fade setup
            min_range_usd = ctx.bar.close * self.min_session_range_pct / 100.0
            if session_range < min_range_usd:
                return None

            # Regime filters
            fear_greed = ctx.features.get("fear_greed_index", 50)
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Avoid trading in crisis or strong trends
            if market_regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
                return None
            
            # Use EMA for trend confirmation (or lack thereof)
            ema_short = self._ema(closes, self.short_ema_period)
            ema_long = self._ema(closes, self.long_ema_period)

            if ema_short is None or ema_long is None:
                return None

            # Only trade if market is somewhat flat (EMAs close)
            if abs(ema_short - ema_long) / current_price > 0.002: # 0.2% difference
                return None

            # Fade conditions
            long_signal = False
            short_signal = False

            # Price is significantly above session mid and near session high -> fade short
            if current_price > session_mid and \
               (current_price - session_mid) / session_range > self.fade_threshold_pct and \
               (self.session_high - current_price) / session_range < 0.1: # Price close to high
                short_signal = True

            # Price is significantly below session mid and near session low -> fade long
            if current_price < session_mid and \
               (session_mid - current_price) / session_range > self.fade_threshold_pct and \
               (current_price - self.session_low) / session_range < 0.1: # Price close to low
                long_signal = True

            confidence = 0.6 + (100 - fear_greed) / 200.0 # Higher confidence if fear is high

            if long_signal and not ctx.has_position():
                self.traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "asia_session_fade_long",
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_mid": self.session_mid,
                    "current_price": current_price,
                    "session_range_pct": (session_range / self.session_open) * 100 if self.session_open else 0,
                    "fear_greed": fear_greed,
                    "market_regime": market_regime
                })

            if short_signal and not ctx.has_position():
                self.traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "asia_session_fade_short",
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_mid": self.session_mid,
                    "current_price": current_price,
                    "session_range_pct": (session_range / self.session_open) * 100 if self.session_open else 0,
                    "fear_greed": fear_greed,
                    "market_regime": market_regime
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position close performance
        pass