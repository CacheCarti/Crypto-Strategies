from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionFade(Strategy):
    METADATA = {
        "name": "EuropeanSessionFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-2 hour hold max
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7 AM
        self.session_end_hour = 12   # UTC 12 PM (noon)
        self.fade_window = 3         # Look back X bars within the session for stretch
        self.atr_period = 14
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.max_stretch_atr = 1.5   # Max stretch in ATR units to consider fading
        self.min_retrace_pct = 0.3   # Min retrace percentage from session high/low to consider fading
        self.min_confidence = 0.5
        self.last_trade_bar_index = -self.warmup_bars # Initialize to allow trading after warmup
        self.cooldown_bars = 5       # Cooldown period after a trade

        # State variables for session tracking
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_bar_count = 0
        self.last_session_day = None
        self.trade_made_this_session = False

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset session data at the start of a new day for session tracking
        if current_day != self.last_session_day:
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open_price = None
            self.session_bar_count = 0
            self.trade_made_this_session = False
            self.last_session_day = current_day

        # Update session high/low/open if within session hours
        if self.session_start_hour <= current_hour < self.session_end_hour:
            if self.session_open_price is None:
                self.session_open_price = ctx.bar.open
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bar_count += 1
        else:
            # Outside session hours, reset session state
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_bar_count = 0

        # Exit logic
        if ctx.has_position():
            # If position is held past session end, exit
            if current_hour >= self.session_end_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position during session if no exit condition met

        # Cooldown period after a trade
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        # Entry logic: Only within European morning hours and if no trade yet this session
        if self.session_start_hour <= current_hour < self.session_end_hour and not self.trade_made_this_session:
            # Ensure we have enough bars for indicators and session context
            if self.session_open_price is None or self.session_bar_count < self.fade_window + 1:
                return None

            atr = self._atr(highs, lows, closes, self.atr_period)
            fast_ema = self._ema(closes, self.fast_ema_period)
            slow_ema = self._ema(closes, self.slow_ema_period)

            if atr is None or fast_ema is None or slow_ema is None:
                return None

            # Regime filters
            fear_greed = ctx.features.get("fear_greed_index", 50)
            funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Do not trade in crisis or strong confirmed trends
            if market_regime in ["CRISIS", "MELTDOWN"] or trend_regime in ["bull", "bear"]:
                return None

            # Calculate stretch from session open
            stretch_up = ctx.bar.close - self.session_open_price
            stretch_down = self.session_open_price - ctx.bar.close

            # Calculate recent volatility (using ATR)
            if atr == 0:
                return None # Avoid division by zero

            normalized_stretch_up = stretch_up / atr
            normalized_stretch_down = stretch_down / atr

            # Fade conditions
            # Long entry: price is significantly stretched down, and showing signs of retrace
            if normalized_stretch_down > self.max_stretch_atr and ctx.bar.close > closes[-2]: # Current close higher than previous
                # Ensure it's not a strong downtrend (EMAs)
                if fast_ema > slow_ema * 0.99: # Allow slight divergence but not a strong bearish trend
                    # Ensure it has retraced a bit from the session low
                    retrace_from_low = (ctx.bar.close - self.session_low) / (self.session_open_price - self.session_low) if self.session_open_price > self.session_low else 0
                    if retrace_from_low >= self.min_retrace_pct:
                        self.trade_made_this_session = True
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=self.min_confidence + (retrace_from_low * 0.3), # Confidence scales with retrace
                            metadata={
                                "reason": "session_fade_long_stretched_down",
                                "session_open": self.session_open_price,
                                "current_price": ctx.bar.close,
                                "normalized_stretch_down": normalized_stretch_down,
                                "retrace_from_low_pct": retrace_from_low,
                                "atr": atr,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "fear_greed": fear_greed,
                                "funding_rate": funding_rate,
                                "market_regime": market_regime,
                                "trend_regime": trend_regime,
                            }
                        )

            # Short entry: price is significantly stretched up, and showing signs of retrace
            if normalized_stretch_up > self.max_stretch_atr and ctx.bar.close < closes[-2]: # Current close lower than previous
                # Ensure it's not a strong uptrend (EMAs)
                if slow_ema > fast_ema * 0.99: # Allow slight divergence but not a strong bullish trend
                    # Ensure it has retraced a bit from the session high
                    retrace_from_high = (self.session_high - ctx.bar.close) / (self.session_high - self.session_open_price) if self.session_high > self.session_open_price else 0
                    if retrace_from_high >= self.min_retrace_pct:
                        self.trade_made_this_session = True
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=self.min_confidence + (retrace_from_high * 0.3), # Confidence scales with retrace
                            metadata={
                                "reason": "session_fade_short_stretched_up",
                                "session_open": self.session_open_price,
                                "current_price": ctx.bar.close,
                                "normalized_stretch_up": normalized_stretch_up,
                                "retrace_from_high_pct": retrace_from_high,
                                "atr": atr,
                                "fast_ema": fast_ema,
                                "slow_ema": slow_ema,
                                "fear_greed": fear_greed,
                                "funding_rate": funding_rate,
                                "market_regime": market_regime,
                                "trend_regime": trend_regime,
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive sizing (not implemented in this version)
        pass