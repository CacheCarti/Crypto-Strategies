from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionImpulseCarry(Strategy):
    METADATA = {
        "name": "US Session Impulse Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Roughly 3 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00
        self.us_session_end_hour = 21   # UTC 21:00
        self.impulse_period = 4         # Bars to measure impulse (4 hours)
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Indicators
        self.ema_short_period = 10
        self.ema_long_period = 30

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate EMAs
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Exit logic
        if ctx.has_position():
            # Close position before US session ends (UTC 21:00)
            if current_hour == self.us_session_end_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # Simple EMA crossover exit (optional, structure stop)
            if ctx.position_direction() == "long" and ema_short < ema_long and closes[-1] < ema_long:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "ema_bear_cross_exit", "ema_short": ema_short, "ema_long": ema_long, "price": closes[-1]})
            if ctx.position_direction() == "short" and ema_short > ema_long and closes[-1] > ema_long:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "ema_bull_cross_exit", "ema_short": ema_short, "ema_long": ema_long, "price": closes[-1]})
            
            return ctx.signal("hold")

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only consider entries at the start of the US session, or during the session
        # and measure impulse from the start of the session.
        if current_hour == self.us_session_start_hour:
            # Mark the start of a new session
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            return None # Wait for impulse to develop

        # Impulse measurement and entry during the US session
        if self.us_session_start_hour < current_hour < self.us_session_end_hour and \
           ctx.bar_index >= self.session_start_bar_index + self.impulse_period:

            # Get price action within the impulse period
            impulse_closes = ctx.closes(ctx.bar_index - self.session_start_bar_index + 1)[-(self.impulse_period + 1):]
            impulse_opens = ctx.opens(ctx.bar_index - self.session_start_bar_index + 1)[-(self.impulse_period + 1):]

            if len(impulse_closes) <= self.impulse_period:
                return None

            # Calculate impulse direction based on the last 'impulse_period' bars within the session
            # Compare current price to the open of the session
            current_price = closes[-1]
            price_change_from_session_open = current_price - self.session_open_price
            
            # Additional confirmation: EMA crossover aligned with impulse
            ema_cross_bullish = ema_short > ema_long and closes[-1] > ema_long
            ema_cross_bearish = ema_short < ema_long and closes[-1] < ema_long

            # Funding rate as a sentiment/regime filter (only if positive for long, negative for short)
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

            # Long entry conditions
            if price_change_from_session_open > 0 and ema_cross_bullish and funding_rate > 0.0001: # Positive impulse, bullish EMA, positive funding
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "us_session_impulse_long",
                        "session_open_price": self.session_open_price,
                        "current_price": current_price,
                        "price_change_pct": (price_change_from_session_open / self.session_open_price) * 100,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "funding_rate": funding_rate
                    }
                )

            # Short entry conditions
            if price_change_from_session_open < 0 and ema_cross_bearish and funding_rate < -0.0001: # Negative impulse, bearish EMA, negative funding
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "us_session_impulse_short",
                        "session_open_price": self.session_open_price,
                        "current_price": current_price,
                        "price_change_pct": (price_change_from_session_open / self.session_open_price) * 100,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "funding_rate": funding_rate
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here based on position performance
        pass