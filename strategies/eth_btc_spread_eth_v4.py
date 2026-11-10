from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class EthBtcSpreadZScore(Strategy):
    METADATA = {
        "name": "EthBtcSpreadZScore",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # 4-hour average hold
        "warmup_bars": 100,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 60 # Period for z-score calculation
        self.zscore_threshold = 1.5 # Z-score threshold for entry
        self.exit_zscore_threshold = 0.2 # Z-score threshold for exit (close to zero)
        self.cooldown_bars = 12 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store recent spread values for z-score calculation
        self.spread_history = collections.deque(maxlen=self.spread_period)

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def _zscore(self, values):
        if len(values) < self.spread_period:
            return 0.0
        mean = sum(values) / len(values)
        std = self._stddev(values)
        if std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Update spread history
        current_spread = eth_return_pct - btc_return_pct
        self.spread_history.append(current_spread)

        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_zscore = self._zscore(list(self.spread_history))

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if spread normalizes or goes negative
                if current_zscore > -self.exit_zscore_threshold: # Exit if spread is no longer significantly negative
                     return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_exit_long",
                        "current_zscore": current_zscore,
                        "exit_threshold": -self.exit_zscore_threshold,
                        "regime": regime,
                        "trend_regime": trend_regime
                    })
            elif ctx.position_direction() == "short":
                # Exit short if spread normalizes or goes positive
                if current_zscore < self.exit_zscore_threshold: # Exit if spread is no longer significantly positive
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_exit_short",
                        "current_zscore": current_zscore,
                        "exit_threshold": self.exit_zscore_threshold,
                        "regime": regime,
                        "trend_regime": trend_regime
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        confidence = 0.0

        # Long entry: ETH underperforming BTC significantly (negative z-score)
        if current_zscore < -self.zscore_threshold:
            # Only long in calm or normal regimes and not in a strong bear trend
            if regime in ["CALM", "NORMAL"] and trend_regime != "bear":
                confidence = min(1.0, abs(current_zscore) / (self.zscore_threshold * 2)) # Scale confidence by how extreme the z-score is
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "eth_btc_spread_oversold_long",
                    "current_zscore": current_zscore,
                    "entry_threshold": -self.zscore_threshold,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "eth_return_pct": eth_return_pct,
                    "btc_return_pct": btc_return_pct
                })

        # Short entry: ETH outperforming BTC significantly (positive z-score)
        elif current_zscore > self.zscore_threshold:
            # Only short in calm or normal regimes and not in a strong bull trend
            if regime in ["CALM", "NORMAL"] and trend_regime != "bull":
                confidence = min(1.0, abs(current_zscore) / (self.zscore_threshold * 2))
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "eth_btc_spread_overbought_short",
                    "current_zscore": current_zscore,
                    "entry_threshold": self.zscore_threshold,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "eth_return_pct": eth_return_pct,
                    "btc_return_pct": btc_return_pct
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on position close
        pass