from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulseBTC(Strategy):
    METADATA = {
        "name": "Asia Session Impulse BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 150.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 3600,  # Aim for 1-2 hours hold, but close by session end
        "warmup_bars": 20,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 0
        self.session_end_hour = 8    # UTC 8
        self.min_session_bars = 4    # Minimum bars to establish session direction
        self.last_trade_bar = -1
        self.cooldown_bars = 4       # Cooldown after a trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # If a position is open, manage its exit based on session end
        if ctx.has_position():
            # If it's close to or past the session end hour (UTC 8), exit
            if current_hour >= self.session_end_hour:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "session_end_hour": self.session_end_hour,
                    "entry_bar": ctx.position["entry_bar_index"]
                })
            # Hold if within session and no other conditions
            return None

        # Entry logic: Only consider entries at the start of the Asia session
        # and only if it's not too close to the end of the session
        if current_hour == self.session_start_hour and ctx.bar_index > self.last_trade_bar + self.cooldown_bars:
            # Check for a strong impulse in the previous session to establish bias
            # For simplicity, let's look at the close-to-close change in the last few hours of the previous session
            # We need enough bars from the previous session to establish direction
            prev_session_closes = ctx.closes(self.min_session_bars + 1) # Need current bar and min_session_bars prior
            
            if len(prev_session_closes) < self.min_session_bars + 1:
                return None # Not enough data for impulse calculation

            # Calculate impulse over the last 'min_session_bars' bars
            # This is a simplified approach, a more robust would track the actual session
            impulse_start_price = prev_session_closes[-(self.min_session_bars + 1)]
            impulse_end_price = closes[-1] # Current bar's close

            price_change = (impulse_end_price - impulse_start_price) / impulse_start_price

            # Use a simple moving average to confirm trend/momentum
            sma_period = 10
            sma = self._sma(closes, sma_period)
            if sma is None:
                return None

            signal_confidence = 0.0

            # Long condition: Positive impulse and price above SMA
            if price_change > 0.001 and closes[-1] > sma: # 0.1% positive impulse
                signal_confidence = 0.7
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "asia_session_impulse_long",
                        "price_change_pct": price_change * 100,
                        "current_price": closes[-1],
                        "sma_10": sma,
                        "entry_bar_index": ctx.bar_index
                    }
                )

            # Short condition: Negative impulse and price below SMA
            elif price_change < -0.001 and closes[-1] < sma: # 0.1% negative impulse
                signal_confidence = 0.7
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "asia_session_impulse_short",
                        "price_change_pct": price_change * 100,
                        "current_price": closes[-1],
                        "sma_10": sma,
                        "entry_bar_index": ctx.bar_index
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close for analysis or adaptive logic
        pass