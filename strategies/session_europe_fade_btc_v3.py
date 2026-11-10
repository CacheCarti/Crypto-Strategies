from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeSessionFadeBTC(Strategy):
    METADATA = {
        "name": "Europe Session Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold 2 hours, but often less for fades
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fade_window_start_hour = 7  # UTC 7 AM
        self.fade_window_end_hour = 12   # UTC 12 PM
        self.entry_cooldown_bars = 5     # Cooldown after a trade
        self.bars_since_last_trade = 0
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.last_session_day = -1
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.rsi_period = 7 # Faster RSI for fade
        self.stddev_period = 20 # For volatility assessment
        self.max_stretch_stddev = 1.5 # How many stddevs price can stretch before fading
        self.max_funding_rate = 0.0003 # Max funding rate to consider fading against
        self.min_fear_greed = 30 # Not too much fear
        self.max_fear_greed = 70 # Not too much greed

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

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day
        current_close = ctx.bar.close

        # Reset session data at the start of a new day
        if current_day != self.last_session_day:
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = None
            self.last_session_day = current_day
            # If a position is open from previous day, exit it
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "new_day_session_reset"})

        # Update session high/low within the fade window
        if self.fade_window_start_hour <= current_hour < self.fade_window_end_hour:
            if self.session_open is None:
                self.session_open = ctx.bar.open # First open of the session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
        elif current_hour >= self.fade_window_end_hour and ctx.has_position():
            # Exit any open position if past the session end hour
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_time_exit", "current_hour": current_hour})

        # Do not trade if a position is open or on cooldown
        if ctx.has_position() or self.bars_since_last_trade < self.entry_cooldown_bars:
            return None

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        current_rsi = self._rsi(closes, self.rsi_period)
        volatility = self._stddev(closes, self.stddev_period)

        if any(x is None for x in [ema_fast, ema_slow, current_rsi, volatility]):
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading in crisis regimes or strong trends
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"] or abs(funding_rate) > self.max_funding_rate:
            return None
        
        # Sentiment filter
        if not (self.min_fear_greed <= fear_greed_index <= self.max_fear_greed):
            return None

        # Session-specific logic
        if self.fade_window_start_hour <= current_hour < self.fade_window_end_hour and self.session_open is not None and volatility > 0:
            stretch_up = (current_close - self.session_open) / volatility
            stretch_down = (self.session_open - current_close) / volatility

            # Long entry: price is significantly below session open, indicating a potential fade upwards
            if stretch_down > self.max_stretch_stddev and current_rsi < 30 and current_close < ema_fast and current_close < ema_slow:
                # Check for transition: price was not significantly stretched down in the previous bar
                prev_close = closes[-2] if len(closes) >= 2 else current_close
                prev_stretch_down = (self.session_open - prev_close) / volatility
                if prev_stretch_down <= self.max_stretch_stddev: # Ensure it's a new stretch
                    self.bars_since_last_trade = 0
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "europe_session_fade_long",
                            "current_hour": current_hour,
                            "session_open": self.session_open,
                            "current_close": current_close,
                            "stretch_down_stddev": stretch_down,
                            "rsi": current_rsi,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "regime": regime,
                            "fear_greed": fear_greed_index,
                        },
                    )

            # Short entry: price is significantly above session open, indicating a potential fade downwards
            elif stretch_up > self.max_stretch_stddev and current_rsi > 70 and current_close > ema_fast and current_close > ema_slow:
                # Check for transition: price was not significantly stretched up in the previous bar
                prev_close = closes[-2] if len(closes) >= 2 else current_close
                prev_stretch_up = (prev_close - self.session_open) / volatility
                if prev_stretch_up <= self.max_stretch_stddev: # Ensure it's a new stretch
                    self.bars_since_last_trade = 0
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "europe_session_fade_short",
                            "current_hour": current_hour,
                            "session_open": self.session_open,
                            "current_close": current_close,
                            "stretch_up_stddev": stretch_up,
                            "rsi": current_rsi,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "regime": regime,
                            "fear_greed": fear_greed_index,
                        },
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.bars_since_last_trade = 0