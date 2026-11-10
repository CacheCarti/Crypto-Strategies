from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFade(Strategy):
    METADATA = {
        "name": "VolatilityFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 40
        self.volatility_period = 20
        self.zscore_threshold = 2.5 # Fade moves beyond this many std devs
        self.volatility_threshold_percentile = 0.75 # Top 25% volatility
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store a history of volatility for percentile calculation
        self.volatility_history = []
        self.max_volatility_history_len = 200 # Keep a reasonable history

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.zscore_period + 1)
        highs = ctx.highs(self.volatility_period + 1)
        lows = ctx.lows(self.volatility_period + 1)
        all_closes = ctx.closes(self.max_volatility_history_len + 1)

        if len(closes) < self.zscore_period + 1 or len(highs) < self.volatility_period + 1 or len(all_closes) < self.volatility_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None

        # Calculate current volatility (using ATR as a proxy)
        current_volatility = self._atr(highs, lows, all_closes, self.volatility_period)
        if current_volatility is None:
            return None

        # Update volatility history
        self.volatility_history.append(current_volatility)
        if len(self.volatility_history) > self.max_volatility_history_len:
            self.volatility_history.pop(0)

        if len(self.volatility_history) < self.max_volatility_history_len / 2: # Need enough history to compute percentile
            return None

        # Determine volatility threshold
        sorted_volatility = sorted(self.volatility_history)
        volatility_index = int(len(sorted_volatility) * self.volatility_threshold_percentile)
        volatility_threshold = sorted_volatility[volatility_index]

        # Regime filter: Only trade in high volatility
        if current_volatility < volatility_threshold:
            return None

        # Regime filter: Do not fade strong trends (e.g., in "bull" or "bear" trend regimes)
        # We want to fade extended moves, not fight strong trends.
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime != "neutral" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None

        signal_direction = None
        confidence = 0.6

        # Long signal: Price is significantly oversold (negative z-score)
        if current_zscore < -self.zscore_threshold:
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence += min(0.3, abs(current_zscore) / (self.zscore_threshold * 2)) # Higher confidence for more extreme moves
                reason = "zscore_oversold_fade_high_vol"

        # Short signal: Price is significantly overbought (positive z-score)
        elif current_zscore > self.zscore_threshold:
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence += min(0.3, abs(current_zscore) / (self.zscore_threshold * 2)) # Higher confidence for more extreme moves
                reason = "zscore_overbought_fade_high_vol"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence),
                metadata={
                    "reason": reason,
                    "zscore": round(current_zscore, 2),
                    "volatility": round(current_volatility, 4),
                    "volatility_threshold": round(volatility_threshold, 4),
                    "trend_regime": trend_regime,
                    "price": ctx.bar.close
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown is handled by on_bar
        pass