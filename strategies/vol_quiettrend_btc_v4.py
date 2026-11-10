from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrendBTC(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_period = 20
        self.volatility_threshold_multiplier = 1.5 # ATR relative to its own EMA
        self.cooldown_bars = 10
        self.last_exit_bar = -self.cooldown_bars # Allow trading from start

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate EMA of ATR for volatility regime
        atr_values = []
        for i in range(self.atr_period + 1, len(ctx.closes(self.atr_period * 2)) + 1):
            h = ctx.highs(i)
            l = ctx.lows(i)
            c = ctx.closes(i)
            atr_val = self._atr(h, l, c, self.atr_period)
            if atr_val is not None:
                atr_values.append(atr_val)
        
        ema_atr = self._ema(atr_values, self.ema_period)
        if ema_atr is None:
            return None

        # Volatility regime check
        is_low_volatility = current_atr < ema_atr * self.volatility_threshold_multiplier
        is_high_volatility = current_atr > ema_atr * self.volatility_threshold_multiplier

        # Trend detection using EMA of closes
        ema_closes = self._ema(closes, self.ema_period)
        if ema_closes is None:
            return None
        
        price_above_ema = ctx.bar.close > ema_closes
        price_below_ema = ctx.bar.close < ema_closes

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Only enter in low volatility and clear trend
        if not ctx.has_position():
            if is_low_volatility:
                # Long entry: price above EMA, indicating uptrend
                if price_above_ema and ctx.bar.close > closes[-2]: # Also check for current bar closing higher than previous
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_uptrend_entry",
                            "current_atr": current_atr,
                            "ema_atr": ema_atr,
                            "close": ctx.bar.close,
                            "ema_closes": ema_closes,
                            "vol_threshold_mult": self.volatility_threshold_multiplier
                        }
                    )
                # Short entry: price below EMA, indicating downtrend
                elif price_below_ema and ctx.bar.close < closes[-2]: # Also check for current bar closing lower than previous
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_downtrend_entry",
                            "current_atr": current_atr,
                            "ema_atr": ema_atr,
                            "close": ctx.bar.close,
                            "ema_closes": ema_closes,
                            "vol_threshold_mult": self.volatility_threshold_multiplier
                        }
                    )
        # Exit logic: Exit on first sign of volatility expansion
        elif ctx.has_position():
            if is_high_volatility:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "current_atr": current_atr,
                        "ema_atr": ema_atr,
                        "close": ctx.bar.close,
                        "position_direction": ctx.position_direction(),
                        "vol_threshold_mult": self.volatility_threshold_multiplier
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index