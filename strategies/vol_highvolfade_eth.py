from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolFade(Strategy):
    METADATA = {
        "name": "EthVolFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 30
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None

        # Volatility check (using ATR as a proxy for volatility)
        atr_value = self._atr(highs, lows, closes, self.volatility_period)
        if atr_value is None:
            return None

        # Calculate a rolling average of ATR to determine "top-quartile volatility"
        all_atrs = []
        for i in range(self.volatility_period, len(closes)):
            atr_i = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.volatility_period)
            if atr_i is not None:
                all_atrs.append(atr_i)

        if len(all_atrs) < self.volatility_period: # Need enough ATR history
            return None

        # Simple percentile check: is current ATR in the top N% of recent ATRs?
        # Sort and pick a threshold. For simplicity, let's say current ATR > 75th percentile of recent ATRs
        sorted_atrs = sorted(all_atrs)
        volatility_threshold = sorted_atrs[int(len(sorted_atrs) * 0.75)]
        
        is_high_volatility = atr_value > volatility_threshold
        
        # Regime filter: do not fade calm trends
        # We define "calm trend" as low volatility and neutral trend regime
        is_calm_trend = not is_high_volatility and ctx.market.get("trend_regime", "neutral") == "neutral"

        if ctx.has_position():
            # If in position, check if z-score has returned to neutral or crossed over
            if ctx.position_direction() == "long" and current_zscore > -0.5:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "zscore_reverted_long_exit", "zscore": current_zscore, "atr": atr_value})
            elif ctx.position_direction() == "short" and current_zscore < 0.5:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "zscore_reverted_short_exit", "zscore": current_zscore, "atr": atr_value})
            return None # Hold position if not ready to exit

        # Entry logic: fade extended z-score moves only in top-quartile volatility and not in a calm trend
        if is_high_volatility and not is_calm_trend:
            if current_zscore < -2.0 and closes[-1] > closes[-2]: # Extreme oversold, confirmed by price rising
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    metadata={
                        "reason": "zscore_fade_oversold_high_vol",
                        "zscore": current_zscore,
                        "atr": atr_value,
                        "volatility_threshold": volatility_threshold,
                        "trend_regime": ctx.market.get("trend_regime")
                    }
                )
            elif current_zscore > 2.0 and closes[-1] < closes[-2]: # Extreme overbought, confirmed by price falling
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    metadata={
                        "reason": "zscore_fade_overbought_high_vol",
                        "zscore": current_zscore,
                        "atr": atr_value,
                        "volatility_threshold": volatility_threshold,
                        "trend_regime": ctx.market.get("trend_regime")
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just logging
        pass