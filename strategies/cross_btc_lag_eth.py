from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetBetaConvergence(Strategy):
    METADATA = {
        "name": "Cross Asset Beta Convergence (ETH)",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_window = 1  # Lookback for BTC return pct
        self.eth_window = 1  # Lookback for ETH return pct
        self.threshold_btc_move = 0.005  # 0.5% move in BTC
        self.threshold_eth_lag = 0.001   # ETH has moved less than 0.1%
        self.ema_period = 20 # for ETH price trend
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.has_position():
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.ema_period + 1:
            return None
        
        current_eth_close = closes[-1]
        
        # Calculate EMA for ETH price trend
        eth_ema = self._ema(closes, self.ema_period)
        if eth_ema is None:
            return None

        # Get BTC and ETH return percentages
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Check for significant BTC move and ETH lag
        # BTC moves up decisively, ETH lags behind
        if btc_return_pct > self.threshold_btc_move and eth_return_pct < self.threshold_eth_lag and current_eth_close > eth_ema:
            # Long signal
            confidence = min(1.0, (btc_return_pct - eth_return_pct) * 50) # Scale confidence based on divergence
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "btc_leader_eth_lag_long",
                    "btc_return_pct": btc_return_pct,
                    "eth_return_pct": eth_return_pct,
                    "eth_close": current_eth_close,
                    "eth_ema20": eth_ema,
                }
            )
        # BTC moves down decisively, ETH lags behind
        elif btc_return_pct < -self.threshold_btc_move and eth_return_pct > -self.threshold_eth_lag and current_eth_close < eth_ema:
            # Short signal
            confidence = min(1.0, (eth_return_pct - btc_return_pct) * 50) # Scale confidence based on divergence
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "btc_leader_eth_lag_short",
                    "btc_return_pct": btc_return_pct,
                    "eth_return_pct": eth_return_pct,
                    "eth_close": current_eth_close,
                    "eth_ema20": eth_ema,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_signal_bar to implement cooldown after position close
        self.last_signal_bar = ctx.bar_index
        # Optional: adaptive sizing based on position['return_bps']
        # For simplicity, we just log here.
        # print(f"Position closed: {position['direction']} with {position['return_bps']} bps return after {position['bars_held']} bars.")