from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionImpulseCarry(Strategy):
    METADATA = {
        "name": "US Session Impulse Carry (BTC)",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,  # Need enough bars for 20-period SMA and ATR
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length_hours = 8 # US Session 13-21 UTC
        self.session_start_hour = 13
        self.session_end_hour = 21 # End of US session
        self.entry_cooldown_bars = 4 # Cooldown after a trade
        self.bars_since_last_entry = self.entry_cooldown_bars + 1 # Initialize to allow first trade

        self.impulse_period = 4 # Bars to measure impulse over
        self.volatility_period = 20 # For ATR
        self.min_impulse_std_dev = 0.5 # Minimum impulse strength in terms of ATR
        self.max_funding_rate = 0.0002 # Max funding rate to consider for long, min for short

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_entry += 1

        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Always exit before the US session ends if a position is open
        if ctx.has_position() and current_hour == self.session_end_hour:
            direction = ctx.position_direction()
            reason = f"session_end_exit_{direction}"
            return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_hour": current_hour})

        # Do not enter if a position is open or on cooldown
        if ctx.has_position() or self.bars_since_last_entry <= self.entry_cooldown_bars:
            return None

        # Only consider entries during the US session (UTC 13-21)
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Calculate impulse during the session
        # We need enough bars for the impulse period
        if len(closes) < self.impulse_period + 1:
            return None

        # Calculate price change over the impulse period
        impulse_start_price = closes[-self.impulse_period - 1]
        impulse_end_price = closes[-1]
        price_change = impulse_end_price - impulse_start_price

        # Calculate ATR for volatility normalization
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None or current_atr == 0:
            return None

        # Normalize impulse by ATR
        normalized_impulse = price_change / current_atr

        # Get historical normalized impulse values to calculate standard deviation
        # This requires storing past normalized impulses or recalculating over a window.
        # For simplicity and performance, we'll use a simple threshold relative to ATR.
        # A more advanced version would use a rolling std dev of normalized_impulse.

        # Long signal: Strong positive impulse
        if normalized_impulse > self.min_impulse_std_dev and funding_rate < self.max_funding_rate:
            self.bars_since_last_entry = 0
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "us_session_impulse_long",
                    "normalized_impulse": normalized_impulse,
                    "price_change": price_change,
                    "atr": current_atr,
                    "funding_rate": funding_rate,
                    "current_hour": current_hour,
                }
            )

        # Short signal: Strong negative impulse
        elif normalized_impulse < -self.min_impulse_std_dev and funding_rate > -self.max_funding_rate:
            self.bars_since_last_entry = 0
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "us_session_impulse_short",
                    "normalized_impulse": normalized_impulse,
                    "price_change": price_change,
                    "atr": current_atr,
                    "funding_rate": funding_rate,
                    "current_hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.bars_since_last_entry = 0