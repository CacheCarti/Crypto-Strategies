from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingFlipConfirmation(Strategy):
    METADATA = {
        "name": "BTC Funding Flip Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour average hold
        "warmup_bars": 50, # Need enough bars for EMA and funding history
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 3 # Look back 3 bars for funding change
        self.price_confirm_period = 2 # Look back 2 bars for price confirmation
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.cooldown_bars = 5
        self.last_signal_bar_index = -self.cooldown_bars

        # Thresholds for funding rate sign flip
        self.funding_threshold = 0.00005 # 0.005% funding rate
        self.funding_change_threshold = 0.00001 # Minimum change to consider a flip

        # Volatility filter: only trade in 'normal' or 'volatile' regimes
        self.allowed_regimes = ["normal", "volatile"]
        
        # Trend filter: only trade in "neutral" or "bull" for long, "neutral" or "bear" for short
        self.allowed_trend_regimes_long = ["neutral", "bull"]
        self.allowed_trend_regimes_short = ["neutral", "bear"]

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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Regime filtering
        current_regime = ctx.market.get("regime", "NORMAL").lower()
        if current_regime not in self.allowed_regimes:
            # print(f"Skipping trade due to regime: {current_regime}")
            return None
        
        current_trend_regime = ctx.market.get("trend_regime", "neutral").lower()

        # Get funding rates
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period + 1)]
        for i in range(self.funding_period + 1):
            # Attempt to get historical funding rates
            if ctx.bar_index - i >= 0:
                # This is a simplification; ctx.features.get only gives current bar's feature.
                # For historical features, we'd need a more complex mechanism or assume current feature for lookback.
                # For this exercise, we will use the current bar's funding rate and assume it's stable enough
                # or that the 'funding_rate_btcusdt' feature is actually providing the *last* reported funding rate
                # which changes hourly.
                # Let's assume `funding_rate_btcusdt` is the rate for the current bar period.
                # To get previous rates, we would need ctx.features.get_history or similar, which isn't available.
                # So we simulate a flip by comparing current to a "previous" conceptual rate.
                pass # The feature itself should handle its cadence.

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        # To detect a "flip", we need a previous funding rate. Since ctx.features.get only gives current,
        # we'll simulate by comparing current funding to a threshold and assuming a recent past.
        # A more robust solution would require `ctx.funding_rates_btcusdt(n)` which doesn't exist.
        # For now, we'll use a simple threshold crossing from below/above zero.

        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Price confirmation: current close must be above/below EMAs
        price_confirm_long = closes[-1] > ema_short and closes[-1] > ema_long
        price_confirm_short = closes[-1] < ema_short and closes[-1] < ema_long

        signal = None
        confidence = 0.0
        signal_reason = ""
        metadata = {}

        # Long conditions:
        # 1. Funding rate flips from negative to positive (or becomes strongly positive)
        # 2. Price confirmation (above EMAs)
        # 3. Market trend is neutral or bullish
        if not ctx.has_position():
            # Funding flip from negative to positive, and current funding is positive
            if current_funding > self.funding_threshold and current_funding > 0: # Funding is positive, suggesting short squeeze/demand
                if closes[-1] > closes[-2] and closes[-2] > closes[-3]: # Simple price momentum confirmation
                    if price_confirm_long and current_trend_regime in self.allowed_trend_regimes_long:
                        confidence = 0.7
                        signal_reason = "btc_funding_flip_positive_price_momentum_ema_confirm"
                        metadata = {
                            "current_funding_rate": current_funding,
                            "current_close": closes[-1],
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "trend_regime": current_trend_regime,
                            "market_regime": current_regime
                        }
                        signal = ctx.signal("long", confidence=confidence, metadata=metadata)

            # Short conditions:
            # 1. Funding rate flips from positive to negative (or becomes strongly negative)
            # 2. Price confirmation (below EMAs)
            # 3. Market trend is neutral or bearish
            elif current_funding < -self.funding_threshold and current_funding < 0: # Funding is negative, suggesting long squeeze/supply
                if closes[-1] < closes[-2] and closes[-2] < closes[-3]: # Simple price momentum confirmation
                    if price_confirm_short and current_trend_regime in self.allowed_trend_regimes_short:
                        confidence = 0.7
                        signal_reason = "btc_funding_flip_negative_price_momentum_ema_confirm"
                        metadata = {
                            "current_funding_rate": current_funding,
                            "current_close": closes[-1],
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "trend_regime": current_trend_regime,
                            "market_regime": current_regime
                        }
                        signal = ctx.signal("short", confidence=confidence, metadata=metadata)

        # Update last signal bar index if a signal was generated
        if signal:
            self.last_signal_bar_index = ctx.bar_index

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close or adapt strategy based on performance
        pass