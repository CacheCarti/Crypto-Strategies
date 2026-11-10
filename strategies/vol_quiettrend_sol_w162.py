from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityTrendRider(Strategy):
    METADATA = {
        "name": "Volatility Trend Rider",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Intended hold for several hours
        "warmup_bars": 50,
        "required_features": [], # No external features used, only OHLCV and context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.volatility_threshold_multiplier = 1.5 # How many ATRs above mean ATR constitutes high vol
        self.min_trend_strength = 0.005 # Minimum EMA difference for a trend
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Indicators
        atr_values = []
        for i in range(self.atr_period, len(closes)):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period)
            if atr_val is not None:
                atr_values.append(atr_val)

        if len(atr_values) < self.atr_period: # Need enough ATR values to calculate mean ATR
            return None

        current_atr = atr_values[-1]
        mean_atr_over_period = sum(atr_values[-self.atr_period:]) / self.atr_period

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        # Volatility Regime Check: Only trade in low volatility
        is_low_volatility = current_atr < mean_atr_over_period * self.volatility_threshold_multiplier

        if ctx.has_position():
            # Exit on first sign of volatility expansion or trend reversal
            if not is_low_volatility:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "volatility_expansion_exit",
                    "current_atr": current_atr,
                    "mean_atr": mean_atr_over_period,
                    "vol_threshold": mean_atr_over_period * self.volatility_threshold_multiplier,
                    "price": current_price
                })

            # Check for trend reversal (EMA cross against current position)
            if ctx.position_direction() == "long" and ema_fast < ema_slow:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "ema_bear_cross_exit",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                })
            elif ctx.position_direction() == "short" and ema_fast > ema_slow:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "ema_bull_cross_exit",
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                })
            return None # Hold position if no exit condition met

        # Entry Conditions (only if no position and in low volatility)
        if is_low_volatility:
            # Long entry: Fast EMA crosses above Slow EMA and trend is strong
            if ema_fast > ema_slow and (ema_fast - ema_slow) / current_price > self.min_trend_strength:
                # Ensure it's a fresh cross, not just continuously above
                prev_ema_fast = self._ema(closes[:-1], self.ema_fast_period)
                prev_ema_slow = self._ema(closes[:-1], self.ema_slow_period)
                if prev_ema_fast is not None and prev_ema_slow is not None and prev_ema_fast <= prev_ema_slow:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=0.9, metadata={
                        "reason": "ema_bull_cross_low_vol_entry",
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_atr": current_atr,
                        "mean_atr": mean_atr_over_period,
                        "price": current_price,
                        "trend_strength": (ema_fast - ema_slow) / current_price
                    })

            # Short entry: Fast EMA crosses below Slow EMA and trend is strong
            elif ema_fast < ema_slow and (ema_slow - ema_fast) / current_price > self.min_trend_strength:
                # Ensure it's a fresh cross, not just continuously below
                prev_ema_fast = self._ema(closes[:-1], self.ema_fast_period)
                prev_ema_slow = self._ema(closes[:-1], self.ema_slow_period)
                if prev_ema_fast is not None and prev_ema_slow is not None and prev_ema_fast >= prev_ema_slow:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=0.9, metadata={
                        "reason": "ema_bear_cross_low_vol_entry",
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_atr": current_atr,
                        "mean_atr": mean_atr_over_period,
                        "price": current_price,
                        "trend_strength": (ema_slow - ema_fast) / current_price
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We don't need to do anything specific here for this strategy,
        # cooldown is handled in on_bar.
        pass