from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL Europe Session Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar, targeting end of session or follow-through
        "warmup_bars": 50, # Needs enough bars for EMA and recent price history
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.impulse_lookback = 5 # Bars to define session impulse
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM (noon)
        self.exit_hour = 12       # UTC 12 PM (noon)
        self.min_impulse_pct = 0.05 # Minimum percentage move to consider an impulse
        self.cooldown_bars = 5     # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Calculate EMAs
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold")
            else:
                return None

        # Exit logic: Close position at the end of the session or if trend reverses
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_session_exit",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                })
            
            # Additional exit condition: EMA crossover implies trend reversal
            if ctx.position_direction() == "long" and ema_fast < ema_slow:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "ema_bear_cross_exit",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": current_close,
                })
            elif ctx.position_direction() == "short" and ema_fast > ema_slow:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "ema_bull_cross_exit",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": current_close,
                })
            
            return ctx.signal("hold")

        # Entry logic: Only within the European morning session (UTC 7-12)
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        # Regime filter: Only trade in 'normal' or 'volatile' regimes, avoid 'crisis'
        if ctx.regime == "crisis":
            return None
        
        # Sentiment filter: Avoid trading during extreme fear
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index < 25: # Extreme Fear
             return None

        # Calculate session impulse
        if len(closes) < self.impulse_lookback + 1:
            return None

        # Get closes for the impulse lookback period
        impulse_closes = closes[-self.impulse_lookback:]
        
        # Check for sufficient price history for impulse calculation
        if len(impulse_closes) < self.impulse_lookback:
            return None

        # Calculate the impulse movement over the last 'impulse_lookback' bars
        # We need the starting price of the impulse window, which is impulse_closes[0]
        # and the ending price, which is impulse_closes[-1] (current_close).
        # To get the price `impulse_lookback` bars ago, we need to access `closes` at `len(closes) - self.impulse_lookback`.
        # The current bar's close is `closes[-1]`.
        
        start_price_impulse = closes[-(self.impulse_lookback + 1)] # Price `impulse_lookback` bars ago
        end_price_impulse = closes[-1] # Current close
        
        price_change_abs = abs(end_price_impulse - start_price_impulse)
        price_change_pct = (price_change_abs / start_price_impulse) * 100

        # Check for significant impulse
        if price_change_pct < self.min_impulse_pct:
            return None

        # Determine direction of impulse and check for EMA alignment
        if end_price_impulse > start_price_impulse: # Upward impulse
            if ema_fast > ema_slow: # Bullish EMA alignment
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "europe_session_bullish_impulse",
                    "entry_hour": current_hour,
                    "price_change_pct": price_change_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed_index": fear_greed_index,
                    "regime": ctx.regime,
                })
        elif end_price_impulse < start_price_impulse: # Downward impulse
            if ema_fast < ema_slow: # Bearish EMA alignment
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "europe_session_bearish_impulse",
                    "entry_hour": current_hour,
                    "price_change_pct": price_change_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed_index": fear_greed_index,
                    "regime": ctx.regime,
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log details of closed positions for analysis
        # print(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']} bps return.")
        pass