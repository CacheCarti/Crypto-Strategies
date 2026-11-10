from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCCarryFunding(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-3 hours average
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # 24 bars for 24-hour average of funding rate
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate EMAs for price confirmation
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close

        # Get funding rates
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # This is a simplification. Ideally, you'd get the historical funding rates.
        # For this contract, ctx.features.get("funding_rate_btcusdt", 0.0) only returns the current bar's value.
        # To get a historical average, you'd need to store past funding rates in self.
        # Given the constraint of getting current funding rate per bar, we'll use a simple approach:
        # Check current funding rate and assume it reflects a trend if persistent.
        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Regime filters
        # Only trade if market is not too volatile or in crisis
        if ctx.market.get("regime") in ["HIGH_VOL", "CRISIS", "MELTDOWN"]:
            return None
        
        # Only trade if trend is not strongly against us
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        signal = None
        confidence = 0.0
        reason = ""
        signal_metadata = {}

        # Long entry conditions:
        # 1. Negative funding rate (pay to short) - implies long demand or short squeeze potential
        # 2. Price above fast EMA, and fast EMA above slow EMA (bullish price momentum)
        # 3. Not in a strong bear trend
        if (current_funding_rate < -0.0001 # Significantly negative funding
            and current_price > ema_fast
            and ema_fast > ema_slow
            and (trend_regime != "bear" or trend_confidence < 0.6)
            and not ctx.has_position()):

            confidence = min(0.7 + (abs(current_funding_rate) * 100), 1.0) # Scale confidence with funding magnitude
            reason = "negative_funding_long_ema_cross_bullish_trend"
            signal_metadata = {
                "funding_rate": current_funding_rate,
                "current_price": current_price,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "trend_regime": trend_regime,
                "trend_confidence": trend_confidence,
            }
            signal = ctx.signal("long", confidence=confidence, metadata=signal_metadata)

        # Short entry conditions:
        # 1. Positive funding rate (pay to long) - implies short demand or long squeeze potential
        # 2. Price below fast EMA, and fast EMA below slow EMA (bearish price momentum)
        # 3. Not in a strong bull trend
        elif (current_funding_rate > 0.0001 # Significantly positive funding
              and current_price < ema_fast
              and ema_fast < ema_slow
              and (trend_regime != "bull" or trend_confidence < 0.6)
              and not ctx.has_position()):

            confidence = min(0.7 + (current_funding_rate * 100), 1.0) # Scale confidence with funding magnitude
            reason = "positive_funding_short_ema_cross_bearish_trend"
            signal_metadata = {
                "funding_rate": current_funding_rate,
                "current_price": current_price,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "trend_regime": trend_regime,
                "trend_confidence": trend_confidence,
            }
            signal = ctx.signal("short", confidence=confidence, metadata=signal_metadata)

        # Exit conditions (normalize funding, or trend reversal)
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if funding normalizes (rate close to zero)
            if abs(current_funding_rate) < 0.00005: # Funding rate close to neutral
                reason = f"funding_rate_normalized_{pos_direction}"
                signal_metadata = {
                    "funding_rate": current_funding_rate,
                    "current_price": current_price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                }
                signal = ctx.signal("flat", confidence=0.8, metadata=signal_metadata)

            # Exit if momentum reverses against position
            elif pos_direction == "long" and ema_fast < ema_slow:
                reason = "ema_cross_bearish_exit_long"
                signal_metadata = {
                    "funding_rate": current_funding_rate,
                    "current_price": current_price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                }
                signal = ctx.signal("flat", confidence=0.7, metadata=signal_metadata)
            elif pos_direction == "short" and ema_fast > ema_slow:
                reason = "ema_cross_bullish_exit_short"
                signal_metadata = {
                    "funding_rate": current_funding_rate,
                    "current_price": current_price,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                }
                signal = ctx.signal("flat", confidence=0.7, metadata=signal_metadata)

        if signal:
            self.last_signal_bar = ctx.bar_index
            return signal
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Add adaptive logic here based on position outcome
        pass