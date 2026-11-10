from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "Asia Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length = 8 # hours (UTC 0-8)
        self.session_start_hour = 0
        self.session_end_hour = self.session_start_hour + self.session_length

        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Indicators for trend and volatility
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14
        self.atr_period = 14
        self.vol_sma_period = 20 # for volume analysis

        self.daily_high = -math.inf
        self.daily_low = math.inf
        self.daily_open = None
        self.daily_close = None
        self.session_bars = [] # Store bars within the current session

        self.traded_this_session = False

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

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_new_day = (current_hour == self.session_start_hour and ctx.bar_index > 0 and ctx.bar.timestamp.day != ctx.closes(2)[0].day) or \
                     (current_hour == self.session_start_hour and ctx.bar_index == 0)

        # Reset daily state at the start of a new session
        if is_new_day:
            self.daily_high = -math.inf
            self.daily_low = math.inf
            self.daily_open = ctx.bar.open
            self.daily_close = None
            self.session_bars = []
            self.traded_this_session = False

        # Update daily/session range
        self.daily_high = max(self.daily_high, ctx.bar.high)
        self.daily_low = min(self.daily_low, ctx.bar.low)
        self.daily_close = ctx.bar.close
        self.session_bars.append(ctx.bar)

        # Exit existing position if end of session is near
        if ctx.has_position() and current_hour == self.session_end_hour - 1 and ctx.bar.timestamp.minute >= 55:
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close, "session_high": self.daily_high, "session_low": self.daily_low})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_price": ctx.bar.close, "session_high": self.daily_high, "session_low": self.daily_low})

        # Do not trade if a position is open or in cooldown
        if ctx.has_position() or ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only trade during the Asia session (UTC 0-8)
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Do not trade if already traded this session
        if self.traded_this_session:
            return None

        # Calculate session midpoint (from open to current high/low)
        if self.daily_open is None or self.daily_high == -math.inf or self.daily_low == math.inf:
            return None

        # Calculate session range and midpoint
        session_range = self.daily_high - self.daily_low
        if session_range == 0: # Avoid division by zero
            return None
        session_midpoint = (self.daily_high + self.daily_low) / 2

        # Filter out strong trends or crisis regimes
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        avg_volume = self._sma(volumes, self.vol_sma_period)

        if ema_fast is None or ema_slow is None or rsi is None or atr is None or avg_volume is None:
            return None

        # Trend filter: Avoid trading against strong trends (e.g., EMA crossover indicating strong trend)
        # We want to fade, so we need a relatively neutral or slightly trending market
        if abs(ema_fast - ema_slow) / ctx.bar.close > 0.005: # If EMA difference is too large, strong trend
            return None

        # Crisis regime filter
        fear_greed = ctx.features.get("fear_greed_index", 50)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        if fear_greed < 25 or abs(btc_return) > 5.0: # Extreme fear or large daily move
            return None

        # Session extension condition: price is near one extreme of the session range
        # and has moved significantly from the session open, but now shows signs of stalling (RSI extreme)
        stretch_threshold_pct = 0.015 # 1.5% stretch from daily open
        stalling_rsi_long = 70 # Overbought RSI for potential short
        stalling_rsi_short = 30 # Oversold RSI for potential long
        
        # Volume confirmation for stalling
        current_volume_ratio = ctx.bar.volume / avg_volume

        # Long signal: price stretched to the downside, now oversold, fade towards midpoint
        if ctx.bar.close < self.daily_open * (1 - stretch_threshold_pct) and \
           rsi < stalling_rsi_short and \
           ctx.bar.close < session_midpoint and \
           current_volume_ratio < 1.0: # Volume decreasing, sign of stalling
            
            self.last_trade_bar = ctx.bar_index
            self.traded_this_session = True
            confidence = 0.6 + (1 - rsi / stalling_rsi_short) * 0.3 # Higher confidence for lower RSI
            return ctx.signal("long", confidence=min(1.0, confidence),
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata={"reason": "asia_session_fade_long",
                                        "current_price": ctx.bar.close,
                                        "daily_open": self.daily_open,
                                        "session_midpoint": session_midpoint,
                                        "rsi": rsi,
                                        "ema_fast": ema_fast,
                                        "ema_slow": ema_slow,
                                        "volume_ratio": current_volume_ratio,
                                        "fear_greed": fear_greed})

        # Short signal: price stretched to the upside, now overbought, fade towards midpoint
        elif ctx.bar.close > self.daily_open * (1 + stretch_threshold_pct) and \
             rsi > stalling_rsi_long and \
             ctx.bar.close > session_midpoint and \
             current_volume_ratio < 1.0: # Volume decreasing, sign of stalling
            
            self.last_trade_bar = ctx.bar_index
            self.traded_this_session = True
            confidence = 0.6 + ((rsi - stalling_rsi_long) / (100 - stalling_rsi_long)) * 0.3 # Higher confidence for higher RSI
            return ctx.signal("short", confidence=min(1.0, confidence),
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata={"reason": "asia_session_fade_short",
                                        "current_price": ctx.bar.close,
                                        "daily_open": self.daily_open,
                                        "session_midpoint": session_midpoint,
                                        "rsi": rsi,
                                        "ema_fast": ema_fast,
                                        "ema_slow": ema_slow,
                                        "volume_ratio": current_volume_ratio,
                                        "fear_greed": fear_greed})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset the traded_this_session flag if the position was closed for any reason
        # and it was opened in the current session.
        # This prevents re-entry within the same session if the position was closed early.
        # However, the strategy explicitly sets traded_this_session=True on entry,
        # so this is more for logging or future adaptive logic.
        pass