from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrendETH(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # ~3 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, only OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

        # Volatility threshold: ATR as percentage of price
        self.low_volatility_threshold_pct = 0.005 # 0.5% ATR of price for low vol
        self.high_volatility_threshold_pct = 0.015 # 1.5% ATR of price for high vol

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Smoothed ATR approximation (SMA of TRs)
        return sum(trs[-period:]) / period

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
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate ATR
        atr_value = self._atr(highs, lows, closes, self.atr_period)
        if atr_value is None:
            return None

        # Determine volatility regime
        volatility_pct = (atr_value / current_price)
        is_low_volatility = volatility_pct < self.low_volatility_threshold_pct
        is_high_volatility = volatility_pct > self.high_volatility_threshold_pct

        # Calculate EMAs for trend
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Exit logic: Exit if volatility expands or trend reverses
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if high volatility or bearish cross
                if is_high_volatility or ema_fast < ema_slow:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_exit_volatility_expansion_or_bearish_cross",
                        "current_vol_pct": volatility_pct,
                        "high_vol_threshold": self.high_volatility_threshold_pct,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price
                    })
            elif ctx.position_direction() == "short":
                # Exit short if high volatility or bullish cross
                if is_high_volatility or ema_fast > ema_slow:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_exit_volatility_expansion_or_bullish_cross",
                        "current_vol_pct": volatility_pct,
                        "high_vol_threshold": self.high_volatility_threshold_pct,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price
                    })
            return None # Hold position if no exit condition met

        # Entry logic: Only enter in low volatility and clear trend
        if is_low_volatility:
            # Long entry: EMA fast crosses above EMA slow
            if closes[-1] > ema_slow and closes[-2] <= self._ema(closes[:-1], self.ema_slow_period) and ema_fast > ema_slow:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "long_entry_low_vol_bullish_ema_cross",
                    "current_vol_pct": volatility_pct,
                    "low_vol_threshold": self.low_volatility_threshold_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                })

            # Short entry: EMA fast crosses below EMA slow
            if closes[-1] < ema_slow and closes[-2] >= self._ema(closes[:-1], self.ema_slow_period) and ema_fast < ema_slow:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "short_entry_low_vol_bearish_ema_cross",
                    "current_vol_pct": volatility_pct,
                    "low_vol_threshold": self.low_volatility_threshold_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, just log
        pass