from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class BtcLateUsSessionFade(Strategy):
    METADATA = {
        "name": "BTC Late US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18  # UTC 18:00 (late US session start)
        self.session_end_hour = 23   # UTC 23:00 (late US session end, trade until midnight UTC)
        self.cooldown_bars = 12      # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_volume_sum = 0.0
        self.session_prices = collections.deque(maxlen=6) # Store last 6 prices for simple momentum check
        self.session_first_bar_index = -1

        # Parameters for detecting stretched move
        self.stretch_threshold_std = 1.5 # How many std devs away from session open for a stretched move
        self.stretch_period = 6 # Lookback for session std dev

        # Indicator periods
        self.ema_period = 20 # For trend filtering
        self.volume_avg_period = 20

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        closes = ctx.closes(self.ema_period + 1)
        volumes = ctx.volumes(self.volume_avg_period)
        if len(closes) < self.ema_period + 1 or len(volumes) < self.volume_avg_period:
            return None

        # Reset session data at the start of a new day (UTC 00:00)
        if current_hour == 0 and self.session_first_bar_index != ctx.bar_index:
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open_price = None
            self.session_volume_sum = 0.0
            self.session_prices.clear()
            self.session_first_bar_index = ctx.bar_index # Mark this as the first bar of the new day

        # Update session data
        self.session_high = max(self.session_high, ctx.bar.high)
        self.session_low = min(self.session_low, ctx.bar.low)
        if self.session_open_price is None:
            self.session_open_price = ctx.bar.open
        self.session_volume_sum += ctx.bar.volume
        self.session_prices.append(ctx.bar.close)

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Determine if we are in the late US session
        is_late_us_session = (self.session_start_hour <= current_hour <= self.session_end_hour)

        # Trend filter (using EMA)
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None
        is_uptrend = ctx.bar.close > ema
        is_downtrend = ctx.bar.close < ema

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        is_crisis = ctx.market.get("crisis_score", 0.0) > 0.5
        is_strong_trend_market = abs(btc_return_pct) > 2.0 or ctx.market.get("trend_regime_confidence", 0.0) > 0.7

        # Calculate session stretch
        if self.session_open_price is None or len(self.session_prices) < self.stretch_period:
            return None

        # Calculate standard deviation of recent session prices relative to session open
        session_price_std = self._stddev(list(self.session_prices), self.stretch_period)
        if session_price_std is None or session_price_std == 0:
            return None

        # Calculate current deviation from session open
        current_deviation = ctx.bar.close - self.session_open_price
        stretched_up = current_deviation > self.stretch_threshold_std * session_price_std
        stretched_down = current_deviation < -self.stretch_threshold_std * session_price_std

        # Midpoint of the current session range
        session_midpoint = (self.session_high + self.session_low) / 2.0 if self.session_high > -math.inf else None

        # Check if we have a position and need to exit
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if session ends or if price crosses midpoint
            if current_hour == 0 or (session_midpoint and
                                    ((pos_direction == "long" and ctx.bar.close > session_midpoint) or
                                     (pos_direction == "short" and ctx.bar.close < session_midpoint))):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_end_or_midpoint_cross_exit",
                    "current_hour": current_hour,
                    "session_midpoint": session_midpoint,
                    "close": ctx.bar.close,
                    "entry_price": entry_price
                })
            return None # Hold position if conditions not met for exit

        # Entry logic (only during late US session, no strong trend, no crisis, and stretched move)
        if is_late_us_session and not is_crisis and not is_strong_trend_market:
            # Short entry: Stretched up, fading towards session midpoint, and not in a strong uptrend
            if stretched_up and not is_uptrend and ctx.bar.close > self.session_open_price:
                # Require price to have pulled back slightly from session high, indicating stall
                if ctx.bar.close < ctx.highs(2)[-2]: # Current close is lower than previous high
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.65, metadata={
                        "reason": "late_us_fade_stretched_up",
                        "session_open": self.session_open_price,
                        "current_deviation": current_deviation,
                        "session_std": session_price_std,
                        "close": ctx.bar.close,
                        "ema": ema,
                        "fear_greed": fear_greed_index
                    })

            # Long entry: Stretched down, fading towards session midpoint, and not in a strong downtrend
            if stretched_down and not is_downtrend and ctx.bar.close < self.session_open_price:
                # Require price to have bounced slightly from session low, indicating stall
                if ctx.bar.close > ctx.lows(2)[-2]: # Current close is higher than previous low
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.65, metadata={
                        "reason": "late_us_fade_stretched_down",
                        "session_open": self.session_open_price,
                        "current_deviation": current_deviation,
                        "session_std": session_price_std,
                        "close": ctx.bar.close,
                        "ema": ema,
                        "fear_greed": fear_greed_index
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session state if a position was closed, to prevent immediate re-entry within the same session after an early exit
        # This reinforces the "only one trade per session" idea
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_volume_sum = 0.0
        self.session_prices.clear()
        self.session_first_bar_index = -1
        pass