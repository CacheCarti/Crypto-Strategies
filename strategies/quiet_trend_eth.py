from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CalmTrendStrategy(Strategy):
    METADATA = {
        "name": "CalmTrendStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for a few bars hold
        "warmup_bars": 100, # Need enough bars for ATR and Z-score
        "required_features": [], # No specific features used beyond market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.volatility_period = 20 # For Z-score of ATR
        self.atr_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade exits
        self.last_signal_bar_index = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Simple moving average of TR
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        
        # Need enough ATR values to calculate its Z-score
        if len(closes) < self.atr_period + self.volatility_period:
             return None

        # Calculate Z-score of ATR to gauge volatility relative to recent history
        atr_values = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        atr_zscore = self._zscore(atr_values, self.volatility_period)

        if any(x is None for x in [fast_ema, slow_ema, current_atr, atr_zscore]):
            return None

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            # Exit if volatility expands (ATR Z-score > 1.0)
            if atr_zscore > 1.0:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "current_atr": current_atr,
                        "atr_zscore": atr_zscore,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                    }
                )
            
            # Exit on regime change from the current trend direction
            if ctx.position_direction() == "long" and trend_regime != "bull":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "trend_regime_change_exit_long",
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                        "market_regime": market_regime,
                    }
                )
            if ctx.position_direction() == "short" and trend_regime != "bear":
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "trend_regime_change_exit_short",
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                        "market_regime": market_regime,
                    }
                )
            return ctx.signal("hold")

        # Entry logic
        # Only trade if market is calm and volatility is not expanding
        if market_regime in ["calm", "normal"] and atr_zscore < 0.5: # Volatility is below average
            current_close = closes[-1]
            prev_close = closes[-2]
            
            # Long entry conditions
            if (trend_regime == "bull" and trend_confidence > 0.6 and
                fast_ema > slow_ema and
                closes[-1] > fast_ema and
                prev_close <= fast_ema): # Close confirmed entry above fast EMA after touching it
                
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75 * trend_confidence, # Scale confidence with trend strength
                    metadata={
                        "reason": "calm_bull_trend_ema_bounce",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_close": current_close,
                        "atr_zscore": atr_zscore,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                    }
                )

            # Short entry conditions
            elif (trend_regime == "bear" and trend_confidence > 0.6 and
                  fast_ema < slow_ema and
                  closes[-1] < fast_ema and
                  prev_close >= fast_ema): # Close confirmed entry below fast EMA after touching it
                
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75 * trend_confidence,
                    metadata={
                        "reason": "calm_bear_trend_ema_bounce",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_close": current_close,
                        "atr_zscore": atr_zscore,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented here, purely for logging if needed
        pass