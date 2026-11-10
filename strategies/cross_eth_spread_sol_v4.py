from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadFade(Strategy):
    METADATA = {
        "name": "SolEthSpreadFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 48 # 2 days of 1-hour bars
        self.zscore_threshold = 2.0
        self.cooldown_bars = 6
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filtering
        current_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Avoid trading in highly volatile or crisis regimes for this mean-reversion strategy
        if current_regime in ["CRISIS", "MELTDOWN", "HIGH_VOL"]:
            return None
        
        # Avoid fading when the underlying asset is strongly trending against the expected fade direction
        if (trend_regime == "bull" and ctx.position_direction() == "short") or \
           (trend_regime == "bear" and ctx.position_direction() == "long"):
            # Allow existing position to run, but prevent new entries
            if not ctx.has_position():
                return None

        # Get recent returns for SOL and ETH
        sol_returns = []
        eth_returns = []
        
        # We need `spread_period` + 1 values to calculate current return for spread
        required_len = self.spread_period + 1

        # Fetch enough historical returns for z-score calculation
        for i in range(required_len):
            sol_ret = ctx.features.get(f"sol_return_pct", 0.0) # Use 0.0 for missing data
            eth_ret = ctx.features.get(f"eth_return_pct", 0.0) # Use 0.0 for missing data

            # For simplicity, we are taking the current bar's return for the spread,
            # assuming `sol_return_pct` and `eth_return_pct` represent the return
            # over the last bar's period. If they are 24h returns, this needs adjustment.
            # For this strategy, we'll assume they are recent % change per bar period.
            sol_returns.append(sol_ret)
            eth_returns.append(eth_ret)

        if len(sol_returns) < required_len or len(eth_returns) < required_len:
            return None

        # Calculate the spread: ETH return minus SOL return
        # A positive spread means ETH outperformed SOL
        # A negative spread means SOL outperformed ETH
        spreads = [eth_r - sol_r for eth_r, sol_r in zip(eth_returns, sol_returns)]

        if len(spreads) < self.spread_period:
            return None

        current_spread_zscore = self._zscore(spreads, self.spread_period)
        
        if current_spread_zscore is None:
            return None

        signal = None
        confidence = 0.0

        if not ctx.has_position():
            # Fade extreme positive spread: ETH outperformed SOL significantly.
            # Expect SOL to catch up or ETH to fall back. Go LONG SOL.
            if current_spread_zscore > self.zscore_threshold:
                confidence = min(0.5 + (current_spread_zscore - self.zscore_threshold) * 0.1, 1.0)
                signal = ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_sol_spread_oversold_sol",
                        "spread_zscore": current_spread_zscore,
                        "eth_return_pct": eth_returns[-1],
                        "sol_return_pct": sol_returns[-1],
                        "regime": current_regime,
                        "trend_regime": trend_regime,
                    }
                )
                self.last_trade_bar = ctx.bar_index

            # Fade extreme negative spread: SOL outperformed ETH significantly.
            # Expect ETH to catch up or SOL to fall back. Go SHORT SOL.
            elif current_spread_zscore < -self.zscore_threshold:
                confidence = min(0.5 + (-current_spread_zscore - self.zscore_threshold) * 0.1, 1.0)
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_sol_spread_overbought_sol",
                        "spread_zscore": current_spread_zscore,
                        "eth_return_pct": eth_returns[-1],
                        "sol_return_pct": sol_returns[-1],
                        "regime": current_regime,
                        "trend_regime": trend_regime,
                    }
                )
                self.last_trade_bar = ctx.bar_index
        else:
            # Exit condition: spread normalizes (z-score comes back towards zero)
            if ctx.position_direction() == "long" and current_spread_zscore < 0.5: # Spread became less positive, or negative
                signal = ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_normalized_long_exit",
                        "spread_zscore": current_spread_zscore,
                        "eth_return_pct": eth_returns[-1],
                        "sol_return_pct": sol_returns[-1],
                    }
                )
                self.last_trade_bar = ctx.bar_index
            elif ctx.position_direction() == "short" and current_spread_zscore > -0.5: # Spread became less negative, or positive
                signal = ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_normalized_short_exit",
                        "spread_zscore": current_spread_zscore,
                        "eth_return_pct": eth_returns[-1],
                        "sol_return_pct": sol_returns[-1],
                    }
                )
                self.last_trade_bar = ctx.bar_index
        
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close details or adapt parameters
        pass