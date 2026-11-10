from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBetaLag(Strategy):
    METADATA = {
        "name": "SOL Beta Lag",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 3  # Bars to check recent returns for BTC/ETH/SOL
        self.min_leader_move_pct = 0.5  # BTC/ETH must move at least this much
        self.max_lag_ratio = 0.5   # SOL's move as a fraction of leader's move
        self.min_lag_diff_pct = 0.2 # Minimum absolute difference in move
        self.cooldown_bars = 5     # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars

        # For regime filtering
        self.volatility_period = 20
        self.min_volatility_zscore = -0.5 # Only trade if volatility is not too low
        self.max_volatility_zscore = 1.5  # Avoid extreme volatility
        self.trend_strength_threshold = 0.4 # Minimum confidence for trend regime

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
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        volatility = self._stddev(ctx.closes(self.volatility_period), self.volatility_period)
        if volatility is None:
            return None
        
        # Z-score of recent volatility relative to its own history (simple proxy)
        # We need more history to compute a meaningful z-score of volatility itself
        # For simplicity, we'll use raw volatility or a simple average
        # As we don't have historical volatility, we'll use a simple threshold
        avg_vol = sum([abs(ctx.closes(self.volatility_period)[i] - ctx.closes(self.volatility_period)[i-1]) for i in range(1, self.volatility_period)]) / (self.volatility_period - 1) if self.volatility_period > 1 else 0
        
        # Skip if volatility is too low or too high (simple heuristic)
        if volatility < ctx.bar.close * 0.001 or volatility > ctx.bar.close * 0.01: # Example thresholds
             # Adjust these thresholds based on domain and desired volatility range
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Calculate SOL's recent return
        sol_recent_return_pct = ((closes[-1] - closes[0]) / closes[0]) * 100.0

        is_long_setup = False
        is_short_setup = False
        reason = ""
        metadata = {}

        # Check for leader movement
        leader_move_pct = 0.0
        if btc_returns > self.min_leader_move_pct and eth_returns > self.min_leader_move_pct:
            leader_move_pct = (btc_returns + eth_returns) / 2.0
            # SOL is lagging on the upside
            if sol_recent_return_pct < leader_move_pct * self.max_lag_ratio and \
               (leader_move_pct - sol_recent_return_pct) > self.min_lag_diff_pct:
                is_long_setup = True
                reason = "btc_eth_strong_up_sol_lagging"
                metadata = {
                    "btc_return": btc_returns,
                    "eth_return": eth_returns,
                    "sol_recent_return": sol_recent_return_pct,
                    "leader_move": leader_move_pct,
                    "lag_diff": leader_move_pct - sol_recent_return_pct,
                }
        elif btc_returns < -self.min_leader_move_pct and eth_returns < -self.min_leader_move_pct:
            leader_move_pct = (btc_returns + eth_returns) / 2.0 # This will be negative
            # SOL is lagging on the downside
            if sol_recent_return_pct > leader_move_pct * self.max_lag_ratio and \
               (sol_recent_return_pct - leader_move_pct) > self.min_lag_diff_pct: # Lagging means less negative
                is_short_setup = True
                reason = "btc_eth_strong_down_sol_lagging"
                metadata = {
                    "btc_return": btc_returns,
                    "eth_return": eth_returns,
                    "sol_recent_return": sol_recent_return_pct,
                    "leader_move": leader_move_pct,
                    "lag_diff": sol_recent_return_pct - leader_move_pct,
                }

        # Apply regime gates
        if is_long_setup:
            if trend_regime == "bear" and trend_confidence > self.trend_strength_threshold:
                is_long_setup = False # Don't long into strong downtrend
                reason += "_bear_trend_filtered"
            elif ctx.regime == "crisis" or ctx.regime == "volatile":
                is_long_setup = False # Avoid longs in very unstable markets
                reason += f"_{ctx.regime}_filtered"
            
        if is_short_setup:
            if trend_regime == "bull" and trend_confidence > self.trend_strength_threshold:
                is_short_setup = False # Don't short into strong uptrend
                reason += "_bull_trend_filtered"
            elif ctx.regime == "crisis":
                is_short_setup = False # Avoid shorts in crisis (often mean reverts up)
                reason += f"_{ctx.regime}_filtered"

        if ctx.has_position():
            # If we have a position and the conditions reverse, consider flipping
            if ctx.position_direction() == "long" and is_short_setup:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.8, metadata={"reason": "flip_long_to_short_" + reason, **metadata})
            elif ctx.position_direction() == "short" and is_long_setup:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.8, metadata={"reason": "flip_short_to_long_" + reason, **metadata})
            # Otherwise, hold current position
            return None
        
        if is_long_setup:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={"reason": reason, **metadata})
        
        if is_short_setup:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={"reason": reason, **metadata})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing for debugging or future adaptive sizing
        pass