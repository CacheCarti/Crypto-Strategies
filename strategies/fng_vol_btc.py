from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedVolReversion(Strategy):
    METADATA = {
        "name": "FearGreedVolReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.fear_greed_threshold = 30 # Extreme Fear/Fear
        self.vol_zscore_threshold = 1.0 # Volatility 1 std dev above average
        self.entry_cooldown_bars = 12 # Wait 12 hours after an exit before new entry
        self.last_exit_bar = -self.entry_cooldown_bars

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
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        if len(closes) < self.vol_period + 1:
            return None

        # Calculate historical volatility (e.g., standard deviation of log returns)
        # Using close-to-close returns for simplicity
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        
        # Calculate current volatility (last `vol_period` returns)
        current_vol = self._stddev(returns, self.vol_period)
        if current_vol is None:
            return None

        # Calculate volatility z-score relative to a longer history (e.g., last 2x vol_period)
        long_term_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        vol_zscore = self._zscore([self._stddev(long_term_returns[i:i+self.vol_period], self.vol_period) for i in range(len(long_term_returns) - self.vol_period + 1) if self._stddev(long_term_returns[i:i+self.vol_period], self.vol_period) is not None], self.vol_period)

        if vol_zscore is None:
            return None
        
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry logic: Buy weakness when F&G is low and volatility is elevated (panic)
        if not ctx.has_position():
            if ctx.bar_index >= self.last_exit_bar + self.entry_cooldown_bars:
                if fear_greed_index <= self.fear_greed_threshold and vol_zscore > self.vol_zscore_threshold:
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "fear_greed_vol_reversion_entry",
                            "fear_greed_index": fear_greed_index,
                            "volatility_zscore": vol_zscore,
                            "current_close": ctx.bar.close
                        }
                    )
        # Exit logic: Close position if volatility normalizes or sentiment recovers
        elif ctx.position_direction() == "long":
            if fear_greed_index > self.fear_greed_threshold + 10 or vol_zscore < 0.0: # F&G recovered or vol normalized
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "fear_greed_vol_reversion_exit",
                        "fear_greed_index": fear_greed_index,
                        "volatility_zscore": vol_zscore,
                        "current_close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the bar index of the last exit for cooldown
        self.last_exit_bar = ctx.bar_index