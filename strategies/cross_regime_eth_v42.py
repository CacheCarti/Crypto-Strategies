from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.spread_threshold = 2.4
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Post-exit cooldown gate to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset spread calculation: spread = ETH 24h return - BTC 24h return
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Regime gate: exclude extreme crisis or meltdown where cross-asset correlations break down
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime, "crisis_score": crisis_score})
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        pos = ctx.position_direction()

        # Exit conditions based on mean reversion completion
        if pos == "long":
            if spread >= 0.2 or rsi >= 68.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "catchup_complete_exit", "spread": spread, "rsi": rsi})
            return None
        elif pos == "short":
            if spread <= -0.2 or rsi <= 32.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "mean_reversion_exit", "spread": spread, "rsi": rsi})
            return None

        # Selective entries: Spread divergence aligned with momentum exhaustion
        # ETH lagging BTC significantly -> Catch-up Long
        if spread <= -self.spread_threshold and rsi < 46.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_catchup_long",
                    "spread": spread,
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "rsi": rsi,
                }
            )

        # ETH outrunning BTC excessively -> Mean Reversion Short
        if spread >= self.spread_threshold and rsi > 54.0:
            confidence = min(0.9, 0.55 + spread * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_reversion_short",
                    "spread": spread,
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "rsi": rsi,
                }
            )

        return None