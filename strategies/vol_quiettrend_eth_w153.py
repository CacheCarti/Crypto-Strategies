from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRegimeTrendRider(Strategy):
    METADATA = {
        "name": "Volatility Regime Trend Rider ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 40
        self.short_period = 10
        self.volatility_period = 20
        self.volatility_threshold_low = 0.005 # ATR % of price, lower bound for "low volatility"
        self.volatility_threshold_high = 0.015 # ATR % of price, upper bound for "low volatility"
        self.min_trend_strength = 0.008 # Minimum EMA divergence for trend
        self.cooldown_bars = 10 # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[-period:]) / period # Simple average for initial EMA
        for v in values[len(values) - period:]: # Recalculate for accuracy
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
        closes = ctx.closes(self.long_period + 1) # Need one extra for ATR
        highs = ctx.highs(self.volatility_period + 1)
        lows = ctx.lows(self.volatility_period + 1)

        if len(closes) < self.long_period + 1 or len(highs) < self.volatility_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Indicators
        ema_short = self._ema(closes, self.short_period)
        ema_long = self._ema(closes, self.long_period)
        atr_value = self._atr(highs, lows, closes, self.volatility_period)

        if ema_short is None or ema_long is None or atr_value is None:
            return None

        # Volatility Regime Filter: ATR as a percentage of price
        atr_percent = atr_value / current_price if current_price != 0 else 0
        is_low_volatility = self.volatility_threshold_low <= atr_percent < self.volatility_threshold_high

        # Trend Strength: Divergence between EMAs
        trend_diff = ema_short - ema_long
        trend_strength_long = trend_diff / current_price > self.min_trend_strength
        trend_strength_short = trend_diff / current_price < -self.min_trend_strength

        # Event Boundary / Clean Setup: Check for recent EMA cross
        # This prevents trading on trends that have been going on for a long time
        # We want to catch the beginning of a sustained trend in low vol.
        prev_ema_short = self._ema(closes[:-1], self.short_period)
        prev_ema_long = self._ema(closes[:-1], self.long_period)

        if prev_ema_short is None or prev_ema_long is None:
            return None

        has_crossed_up_recently = (prev_ema_short <= prev_ema_long and ema_short > ema_long)
        has_crossed_down_recently = (prev_ema_short >= prev_ema_long and ema_short < ema_long)

        # Confidence based on how far into the low vol regime we are or trend strength
        confidence = 0.5 + min(0.5, abs(atr_percent - self.volatility_threshold_low) / (self.volatility_threshold_high - self.volatility_threshold_low))

        # Check for existing position
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if volatility expands or trend reverses
                if atr_percent > self.volatility_threshold_high or not trend_strength_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_exit_volatility_expansion_or_trend_reverse",
                        "current_atr_pct": atr_percent,
                        "vol_threshold_high": self.volatility_threshold_high,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "trend_strength_long": trend_strength_long
                    })
            elif ctx.position_direction() == "short":
                # Exit short if volatility expands or trend reverses
                if atr_percent > self.volatility_threshold_high or not trend_strength_short:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_exit_volatility_expansion_or_trend_reverse",
                        "current_atr_pct": atr_percent,
                        "vol_threshold_high": self.volatility_threshold_high,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "trend_strength_short": trend_strength_short
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        if is_low_volatility:
            if trend_strength_long and has_crossed_up_recently:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "low_vol_ema_bull_cross_trend_start",
                    "current_atr_pct": atr_percent,
                    "vol_threshold_low": self.volatility_threshold_low,
                    "vol_threshold_high": self.volatility_threshold_high,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "trend_diff_pct": trend_diff / current_price,
                    "min_trend_strength": self.min_trend_strength,
                    "price": current_price
                })
            elif trend_strength_short and has_crossed_down_recently:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "low_vol_ema_bear_cross_trend_start",
                    "current_atr_pct": atr_percent,
                    "vol_threshold_low": self.volatility_threshold_low,
                    "vol_threshold_high": self.volatility_threshold_high,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "trend_diff_pct": trend_diff / current_price,
                    "min_trend_strength": self.min_trend_strength,
                    "price": current_price
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar on signal generation for cooldown
        # This method can be used for logging or adaptive sizing if needed.
        pass