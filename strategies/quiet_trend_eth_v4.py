from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class CalmTrendFollower(Strategy):
    METADATA = {
        "name": "CalmTrendFollower",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 100,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.volatility_period = 60 # For ATR
        self.volatility_median_period = 20 # For comparing current ATR to recent median
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Calculate ATR for volatility
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        # Get historical ATRs to compare with median
        past_atrs = []
        for i in range(self.volatility_median_period + self.volatility_period - 1, len(closes)):
            # This is an approximation for efficiency. In a real scenario, you'd precompute or store.
            # For backtesting, we can recompute a slice if necessary, but this is faster.
            # A more robust way would be to store past ATRs in initialize or on_bar
            # For simplicity and to avoid storing large lists across bars, we'll use a slice.
            if i >= self.volatility_period:
                past_atrs.append(self._atr(highs[i-self.volatility_period+1:i+1], lows[i-self.volatility_period+1:i+1], closes[i-self.volatility_period+1:i+1], self.volatility_period))
        
        if len(past_atrs) < self.volatility_median_period:
            return None
        
        recent_atrs = [atr for atr in past_atrs[-self.volatility_median_period:] if atr is not None]
        if not recent_atrs:
            return None
        
        median_atr = statistics.median(recent_atrs)

        # Volatility filter: current ATR must be below its recent median
        is_calm_volatility = current_atr < median_atr * 0.95 # Factor to ensure it's significantly below median

        # Trend filter: market context trend regime and EMA crossover
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        
        # Check for EMA crossover (transition)
        prev_fast_ema = self._ema(closes[:-1], self.fast_ema_period)
        prev_slow_ema = self._ema(closes[:-1], self.slow_ema_period)

        long_condition = False
        short_condition = False
        signal_reason = ""
        signal_confidence = 0.0

        if not ctx.has_position():
            # Long entry conditions
            if is_calm_volatility and \
               trend_regime == "bull" and trend_confidence > 0.6 and \
               fast_ema > slow_ema and \
               prev_fast_ema <= prev_slow_ema: # Golden Cross
                
                long_condition = True
                signal_reason = "calm_bull_trend_ema_cross"
                signal_confidence = min(0.7, trend_confidence * 0.8) # Scale confidence with trend strength

            # Short entry conditions
            elif is_calm_volatility and \
                 trend_regime == "bear" and trend_confidence > 0.6 and \
                 fast_ema < slow_ema and \
                 prev_fast_ema >= prev_slow_ema: # Death Cross
                
                short_condition = True
                signal_reason = "calm_bear_trend_ema_cross"
                signal_confidence = min(0.7, trend_confidence * 0.8) # Scale confidence with trend strength

            if long_condition:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("long", confidence=signal_confidence, metadata={
                    "reason": signal_reason,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_atr": current_atr,
                    "median_atr": median_atr,
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence
                })
            elif short_condition:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("short", confidence=signal_confidence, metadata={
                    "reason": signal_reason,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_atr": current_atr,
                    "median_atr": median_atr,
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence
                })
        else:
            # Exit conditions: Volatility expansion or trend regime change
            if ctx.position_direction() == "long":
                if not is_calm_volatility or \
                   (trend_regime != "bull" and trend_confidence > 0.5): # Exit if volatility expands or bull trend weakens significantly
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_exit_volatility_or_trend_change",
                        "is_calm_volatility": is_calm_volatility,
                        "current_atr": current_atr,
                        "median_atr": median_atr,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence
                    })
            elif ctx.position_direction() == "short":
                if not is_calm_volatility or \
                   (trend_regime != "bear" and trend_confidence > 0.5): # Exit if volatility expands or bear trend weakens significantly
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_exit_volatility_or_trend_change",
                        "is_calm_volatility": is_calm_volatility,
                        "current_atr": current_atr,
                        "median_atr": median_atr,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, just logging
        pass