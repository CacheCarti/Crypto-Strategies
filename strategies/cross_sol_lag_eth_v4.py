from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSolEthLag(Strategy):
    METADATA = {
        "name": "CrossAssetSolEthLag",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.sol_lookback_period = 5
        self.eth_lookback_period = 5
        self.min_sol_move_pct = 0.5  # SOL must move at least 0.5%
        self.max_eth_lag_pct = 0.2   # ETH must lag by no more than 0.2% in the same direction
        self.cooldown_bars = 10      # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For regime filtering
        self.volatility_period = 20
        self.min_volatility_zscore = -0.5 # Only trade if volatility is not too low
        self.max_volatility_zscore = 1.5  # Avoid trading in extreme volatility

        # EMA for trend filtering
        self.ema_fast_period = 20
        self.ema_slow_period = 50

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filtering: Volatility
        volatility_zscore = self._zscore([c / closes[i-1] - 1 for i, c in enumerate(closes) if i > 0], self.volatility_period)
        if volatility_zscore is None or not (self.min_volatility_zscore < volatility_zscore < self.max_volatility_zscore):
            return None

        # Regime filtering: Trend
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        trend_regime = "neutral"
        if ema_fast > ema_slow * 1.001: # 0.1% buffer for clear trend
            trend_regime = "bull"
        elif ema_fast < ema_slow * 0.999:
            trend_regime = "bear"

        # Get SOL and ETH returns
        sol_returns = ctx.features.get("sol_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        signal_direction = None
        reason = ""
        confidence = 0.0
        metadata = {}

        # Long condition: SOL makes a fresh move up, ETH lags, and market is not bearish
        if sol_returns > self.min_sol_move_pct and eth_returns < sol_returns - self.max_eth_lag_pct:
            if trend_regime != "bear":
                signal_direction = "long"
                reason = "sol_leads_eth_long_lag"
                confidence = min(1.0, (sol_returns - eth_returns) * 2.0) # Scale confidence by lag magnitude
                metadata = {
                    "reason": reason,
                    "sol_return_pct": sol_returns,
                    "eth_return_pct": eth_returns,
                    "lag_pct": sol_returns - eth_returns,
                    "volatility_zscore": volatility_zscore,
                    "trend_regime": trend_regime
                }

        # Short condition: SOL makes a fresh move down, ETH lags, and market is not bullish
        elif sol_returns < -self.min_sol_move_pct and eth_returns > sol_returns + self.max_eth_lag_pct:
            if trend_regime != "bull":
                signal_direction = "short"
                reason = "sol_leads_eth_short_lag"
                confidence = min(1.0, (eth_returns - sol_returns) * 2.0) # Scale confidence by lag magnitude
                metadata = {
                    "reason": reason,
                    "sol_return_pct": sol_returns,
                    "eth_return_pct": eth_returns,
                    "lag_pct": eth_returns - sol_returns,
                    "volatility_zscore": volatility_zscore,
                    "trend_regime": trend_regime
                }

        if ctx.has_position():
            if ctx.position_direction() == "long" and signal_direction == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "flip_to_short_sol_lag", **metadata})
            elif ctx.position_direction() == "short" and signal_direction == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "flip_to_long_sol_lag", **metadata})
            else: # Hold position if no strong opposing signal
                return None
        elif signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata=metadata)

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log closing info or adapt strategy parameters
        pass