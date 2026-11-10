from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetZScoreFade(Strategy):
    METADATA = {
        "name": "Cross Asset Z-Score Fade BTC-ETH",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold on average
        "warmup_bars": 100,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 60 # For z-score calculation (60 hours)
        self.zscore_threshold = 1.5 # Entry threshold for z-score
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # We need historical return data for z-score. We store them in a list.
        # This is okay for a limited lookback, but don't store indefinitely.
        if not hasattr(self, 'btc_return_history'):
            self.btc_return_history = []
            self.eth_return_history = []

        self.btc_return_history.append(btc_returns)
        self.eth_return_history.append(eth_returns)

        if len(self.btc_return_history) > self.lookback_period:
            self.btc_return_history.pop(0)
            self.eth_return_history.pop(0)

        if len(self.btc_return_history) < self.lookback_period:
            return None

        # Calculate the spread: BTC return - ETH return (leader - target)
        # We are trading BTC, so ETH is the leader/causal driver
        spread_history = [b - e for b, e in zip(self.btc_return_history, self.eth_return_history)]

        current_spread_zscore = self._zscore(spread_history, self.lookback_period)

        # Regime filter: only trade in 'normal' or 'volatile' regimes, avoid 'crisis' and 'meltdown'
        # Also avoid trading if the market trend is strongly against our fade direction
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        if not ctx.has_position():
            # Long signal: BTC underperforming ETH significantly (negative spread z-score)
            if current_spread_zscore < -self.zscore_threshold:
                # Only long if not in a strong bear trend
                if trend_regime != "bear":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=min(1.0, abs(current_spread_zscore) / (2 * self.zscore_threshold)),
                        metadata={
                            "reason": "btc_eth_spread_oversold_fade",
                            "current_spread_zscore": current_spread_zscore,
                            "zscore_threshold": -self.zscore_threshold,
                            "btc_return_pct": btc_returns,
                            "eth_return_pct": eth_returns,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                        },
                    )

            # Short signal: BTC outperforming ETH significantly (positive spread z-score)
            elif current_spread_zscore > self.zscore_threshold:
                # Only short if not in a strong bull trend
                if trend_regime != "bull":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=min(1.0, abs(current_spread_zscore) / (2 * self.zscore_threshold)),
                        metadata={
                            "reason": "btc_eth_spread_overbought_fade",
                            "current_spread_zscore": current_spread_zscore,
                            "zscore_threshold": self.zscore_threshold,
                            "btc_return_pct": btc_returns,
                            "eth_return_pct": eth_returns,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                        },
                    )

        else: # If position is open
            position_direction = ctx.position_direction()
            
            # Exit condition: Z-score normalizes (crosses zero towards normalization)
            if position_direction == "long" and current_spread_zscore > -0.5: # Moved back towards zero or positive
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_long_exit",
                        "current_spread_zscore": current_spread_zscore,
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                    },
                )
            elif position_direction == "short" and current_spread_zscore < 0.5: # Moved back towards zero or negative
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_short_exit",
                        "current_spread_zscore": current_spread_zscore,
                        "btc_return_pct": btc_returns,
                        "eth_return_pct": eth_returns,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly needed for this strategy, but good practice for adaptive logic
        pass