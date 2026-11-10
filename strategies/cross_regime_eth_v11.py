from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Spread Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.spread_threshold = 2.2  # 2.2% divergence threshold
        self.rsi_period = 14
        self.rsi_long_bound = 55.0   # ETH not already overbought
        self.rsi_short_bound = 45.0  # ETH not already oversold

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme crisis regimes to protect capital
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Cross-asset returns (24h return percentage)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        
        # Spread: positive means ETH leads BTC, negative means ETH lags BTC
        spread = eth_ret - btc_ret

        # Calculate local RSI for ETH
        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Mean reversion profit-taking / dynamic convergence exit
            if pos_dir == "long" and spread >= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_long", "spread": spread, "rsi": rsi_val}
                )
            elif pos_dir == "short" and spread <= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_short", "spread": spread, "rsi": rsi_val}
                )
            return None

        # Entry logic: enforce cooldown after previous trade closure
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup: BTC leads ETH significantly, ETH not already overextended upwards
        if spread <= -self.spread_threshold and rsi_val < self.rsi_long_bound:
            conf = min(0.9, max(0.5, abs(spread) / 4.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val
                }
            )

        # Short Setup: ETH leads BTC excessively, ETH not already oversold
        if spread >= self.spread_threshold and rsi_val > self.rsi_short_bound:
            conf = min(0.9, max(0.5, abs(spread) / 4.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lead_reversion_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val
                }
            )

        return None