from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "AsiaSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.min_session_range_pct = 0.5 # Minimum range for a fade opportunity
        self.max_session_range_pct = 3.0 # Max range to consider fading (avoid extreme breakouts)
        self.fade_threshold_pct = 0.5 # How far from open to consider fading
        self.min_bars_in_session = 3 # Minimum bars into session to consider entry
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For trend filtering
        self.ema_period_fast = 20
        self.ema_period_slow = 50

        # Track session data
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.traded_this_session = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_asia_session = self.session_start_hour <= current_hour < self.session_end_hour

        # Session management
        if is_asia_session and self.session_start_bar_index == -1:
            # New session starts
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            self.traded_this_session = False
        elif is_asia_session:
            # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
        elif not is_asia_session and self.session_start_bar_index != -1:
            # Session ended, reset
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_bar_index = -1
            self.traded_this_session = False

        # Exit logic: close before session end or if regime changes
        if ctx.has_position():
            if current_hour >= self.session_end_hour - 1: # Exit one hour before session end
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # If market becomes too volatile, exit
            if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "crisis_regime_exit", "regime": ctx.market.get("regime")})

        # Entry logic
        if not is_asia_session or ctx.has_position() or self.traded_this_session:
            return None
        
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if self.session_open_price is None or ctx.bar_index - self.session_start_bar_index < self.min_bars_in_session:
            return None

        # Regime filters
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 20 or fear_greed > 80: # Avoid extreme sentiment
            return None

        # Trend filter: only fade if not in a strong trend
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        if ema_fast is None or ema_slow is None:
            return None
        
        # If EMAs are strongly diverging, it's a strong trend, don't fade
        if abs(ema_fast - ema_slow) / ctx.bar.close > 0.005: # 0.5% spread
            return None

        session_range = self.session_high - self.session_low
        session_range_pct = (session_range / self.session_open_price) * 100

        if not (self.min_session_range_pct <= session_range_pct <= self.max_session_range_pct):
            return None

        # Calculate midpoint for fade target
        session_midpoint = (self.session_high + self.session_low) / 2

        # Fade conditions
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Price is significantly above session open and stalling near high
        if ctx.bar.close > self.session_open_price * (1 + self.fade_threshold_pct / 100) and \
           ctx.bar.close > self.session_high * 0.995 and \
           ctx.bar.close > session_midpoint: # Ensure we are above midpoint
            
            # Check for bearish reversal candle (e.g., small body, long upper wick)
            if (ctx.bar.open > ctx.bar.close and (ctx.bar.high - ctx.bar.open) > 2 * (ctx.bar.open - ctx.bar.close)) or \
               (ctx.bar.open < ctx.bar.close and (ctx.bar.high - ctx.bar.close) > 2 * (ctx.bar.close - ctx.bar.open)):
                signal_direction = "short"
                confidence = min(0.7, session_range_pct / self.max_session_range_pct) # Higher range, higher confidence
                reason = "asia_session_fade_short_stretched_high"

        # Price is significantly below session open and stalling near low
        elif ctx.bar.close < self.session_open_price * (1 - self.fade_threshold_pct / 100) and \
             ctx.bar.close < self.session_low * 1.005 and \
             ctx.bar.close < session_midpoint: # Ensure we are below midpoint
            
            # Check for bullish reversal candle (e.g., small body, long lower wick)
            if (ctx.bar.open < ctx.bar.close and (ctx.bar.close - ctx.bar.low) > 2 * (ctx.bar.close - ctx.bar.open)) or \
               (ctx.bar.open > ctx.bar.close and (ctx.bar.open - ctx.bar.low) > 2 * (ctx.bar.open - ctx.bar.close)):
                signal_direction = "long"
                confidence = min(0.7, session_range_pct / self.max_session_range_pct)
                reason = "asia_session_fade_long_stretched_low"

        if signal_direction:
            self.traded_this_session = True
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "session_open": self.session_open_price,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_range_pct": round(session_range_pct, 2),
                    "session_midpoint": round(session_midpoint, 2),
                    "current_price": ctx.bar.close,
                    "fear_greed": fear_greed,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, reset traded_this_session if position closes early
        # For this strategy, we want to ensure only one trade per session regardless of outcome
        pass