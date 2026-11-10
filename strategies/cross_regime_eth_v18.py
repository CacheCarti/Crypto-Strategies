from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadCatchUp(Strategy):
    METADATA = {
        "name": "EthBtcSpreadCatchUp",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spread_threshold = 2.2  # 2.2% return discrepancy
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        # Check market regime to avoid entering during extreme market panics
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # Positive: BTC outperforming ETH (ETH lagging)

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position exit management on spread convergence
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread <= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_eth_caught_up", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and spread >= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_btc_caught_up", "spread": spread, "rsi": rsi}
                )
            return None

        # Cooldown guard: enforce post-exit spacing to prevent friction death
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Event: BTC strong up-move, ETH materially lagging -> Long ETH for catch-up
        # Gated by RSI not being overbought (< 55)
        if spread >= self.spread_threshold and btc_ret > 0.8 and rsi < 55.0:
            conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "spread": spread,
                    "rsi": rsi
                }
            )

        # Event: ETH severely outrunning BTC -> Short ETH expecting spread normalization
        # Gated by RSI not being oversold (> 45)
        if spread <= -self.spread_threshold and eth_ret > 0.8 and rsi > 45.0:
            conf = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_revert_short",
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "spread": spread,
                    "rsi": rsi
                }
            )

        return None