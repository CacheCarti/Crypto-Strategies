from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class VolatilityQuietTrendBTC(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Intended for multi-hour holds
        "warmup_bars": 60, # ATR period + SMA/StdDev period
        "required_features": [], # No specific features needed beyond OHLCV and regime
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.sma_period = 20
        self.stddev_period = 20
        self.cooldown_bars = 12 # Cooldown for 12 hours after an exit
        self.last_exit_bar = -self.cooldown_bars # Initialize to allow immediate entry

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close
        
        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.atr_period)
        sma = self._sma(closes, self.sma_period)
        stddev = self._stddev(closes, self.stddev_period)

        if atr is None or sma is None or stddev is None:
            return None

        # Volatility filter: only trade in low volatility regimes
        # We define low volatility as when the current ATR is below its recent average,
        # and the standard deviation is also relatively low.
        atr_history = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        atr_history = [a for a in atr_history if a is not None]
        
        if len(atr_history) < self.stddev_period: # Need enough history for SMA of ATR
            return None

        avg_atr = self._sma(atr_history, self.stddev_period) # SMA of ATR for a longer period
        
        if avg_atr is None:
            return None

        is_low_volatility = (atr < avg_atr * 0.9) and (stddev < closes[-1] * 0.01) # ATR below 90% of its avg, and low stddev relative to price

        # Trend filter: only trade if price is above/below a moving average AND in a "normal" regime
        is_uptrend = current_price > sma
        is_downtrend = current_price < sma
        
        is_normal_regime = ctx.regime == "normal" or ctx.regime == "calm"

        # Cooldown period
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic
        if not ctx.has_position():
            # Long entry: low volatility, clear uptrend, normal/calm regime
            if is_low_volatility and is_uptrend and is_normal_regime:
                # Ensure the current bar is a strong close higher
                prev_close = closes[-2] if len(closes) >= 2 else None
                if prev_close and current_price > prev_close:
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_uptrend_entry",
                            "current_price": current_price,
                            "sma_20": sma,
                            "atr_14": atr,
                            "avg_atr_long": avg_atr,
                            "stddev_20": stddev,
                            "regime": ctx.regime
                        }
                    )
            
            # Short entry: low volatility, clear downtrend, normal/calm regime
            elif is_low_volatility and is_downtrend and is_normal_regime:
                # Ensure the current bar is a strong close lower
                prev_close = closes[-2] if len(closes) >= 2 else None
                if prev_close and current_price < prev_close:
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_downtrend_entry",
                            "current_price": current_price,
                            "sma_20": sma,
                            "atr_14": atr,
                            "avg_atr_long": avg_atr,
                            "stddev_20": stddev,
                            "regime": ctx.regime
                        }
                    )

        # Exit logic: exit on first sign of volatility expansion
        if ctx.has_position():
            # Volatility expansion detected if ATR significantly increases
            is_volatility_expanding = atr > avg_atr * 1.2 # ATR is 20% higher than its recent average
            
            if is_volatility_expanding:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "current_price": current_price,
                        "atr_14": atr,
                        "avg_atr_long": avg_atr,
                        "stddev_20": stddev,
                        "regime": ctx.regime
                    }
                )
            
            # Exit if trend reverses (price crosses SMA)
            if ctx.position_direction() == "long" and current_price < sma:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_trend_reversal_exit",
                        "current_price": current_price,
                        "sma_20": sma,
                        "atr_14": atr,
                        "regime": ctx.regime
                    }
                )
            elif ctx.position_direction() == "short" and current_price > sma:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_trend_reversal_exit",
                        "current_price": current_price,
                        "sma_20": sma,
                        "atr_14": atr,
                        "regime": ctx.regime
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy,
        # but could be used to adjust cooldown or confidence based on performance.
        pass