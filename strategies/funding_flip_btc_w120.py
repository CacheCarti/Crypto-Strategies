from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipConfirmation(Strategy):
    METADATA = {
        "name": "Funding Flip Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24 bars for funding rate check (24 hours)
        self.confirmation_period = 3 # Bars to confirm price direction after funding flip
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State to track funding rate flips and price confirmation
        self.last_funding_sign = 0 # 1 for positive, -1 for negative, 0 for neutral/first bar
        self.funding_flip_bar = -1 # Bar index when a flip occurred
        self.flip_direction = 0 # 1 for long, -1 for short

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

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Determine current funding sign
        current_funding_sign = 0
        if current_funding_rate > 0.0001: # Positive funding
            current_funding_sign = 1
        elif current_funding_rate < -0.0001: # Negative funding
            current_funding_sign = -1

        # Check for funding flip
        if self.last_funding_sign == 0: # Initialize on first meaningful funding rate
            self.last_funding_sign = current_funding_sign
        elif current_funding_sign != 0 and current_funding_sign != self.last_funding_sign:
            self.funding_flip_bar = ctx.bar_index
            self.flip_direction = current_funding_sign # +1 for positive flip (long), -1 for negative flip (short)
            self.last_funding_sign = current_funding_sign
            # Reset confirmation state as a new flip just happened
            return None

        # Check for price confirmation after a funding flip
        if self.funding_flip_bar != -1 and ctx.bar_index > self.funding_flip_bar:
            bars_since_flip = ctx.bar_index - self.funding_flip_bar

            if bars_since_flip <= self.confirmation_period:
                # Calculate EMAs for price confirmation
                ema_short = self._ema(closes, self.ema_short_period)
                ema_long = self._ema(closes, self.ema_long_period)

                if ema_short is None or ema_long is None:
                    return None

                # Long signal: funding flipped positive AND short EMA crossed above long EMA
                if self.flip_direction == 1 and ema_short > ema_long and closes[-1] > ema_short:
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        self.funding_flip_bar = -1 # Reset flip state after signal
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "funding_positive_flip_ema_bull_cross",
                                "funding_rate": current_funding_rate,
                                "ema_short": ema_short,
                                "ema_long": ema_long,
                                "price": closes[-1],
                                "bars_since_flip": bars_since_flip
                            }
                        )
                # Short signal: funding flipped negative AND short EMA crossed below long EMA
                elif self.flip_direction == -1 and ema_short < ema_long and closes[-1] < ema_short:
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        self.funding_flip_bar = -1 # Reset flip state after signal
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "funding_negative_flip_ema_bear_cross",
                                "funding_rate": current_funding_rate,
                                "ema_short": ema_short,
                                "ema_long": ema_long,
                                "price": closes[-1],
                                "bars_since_flip": bars_since_flip
                            }
                        )
            else:
                # If confirmation period passed without a signal, reset flip state
                self.funding_flip_bar = -1
                self.flip_direction = 0

        # Position management: exit if EMAs cross against current position or funding flips back
        if ctx.has_position():
            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)

            if ema_short is None or ema_long is None:
                return None

            if ctx.position_direction() == "long":
                # Exit long if EMAs cross bearish or funding turns negative
                if (ema_short < ema_long) or (current_funding_rate < -0.0001):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_ema_bear_cross_or_funding_flip_negative",
                            "current_funding_rate": current_funding_rate,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "price": closes[-1]
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if EMAs cross bullish or funding turns positive
                if (ema_short > ema_long) or (current_funding_rate > 0.0001):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_ema_bull_cross_or_funding_flip_positive",
                            "current_funding_rate": current_funding_rate,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "price": closes[-1]
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, but could be added here.
        pass