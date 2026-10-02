from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSpreadCatchup(Strategy):
    METADATA = {
        "name": "CrossAssetSpreadCatchup",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 0.65
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_trade_bar = -999

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        # Market regime filter - avoid trading in meltdown / extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret

        pos_dir = ctx.position_direction()

        # Handle active position exits via mean reversion of spread
        if pos_dir == "long":
            if spread <= 0.0 or rsi >= 70.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "spread_reverted_or_rsi_target",
                        "spread": spread,
                        "btc_ret": btc_ret,
                        "eth_ret": eth_ret,
                        "rsi": rsi,
                    },
                )
            return None

        if pos_dir == "short":
            if spread >= 0.0 or rsi <= 30.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "spread_reverted_or_rsi_target",
                        "spread": spread,
                        "btc_ret": btc_ret,
                        "eth_ret": eth_ret,
                        "rsi": rsi,
                    },
                )
            return None

        # Cooldown guard for new entries
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Long Setup: BTC leads, ETH lags (spread positive), avoid entering into extreme overbought
        if spread >= self.spread_threshold and rsi < 68.0:
            confidence = min(0.9, 0.55 + max(0.0, spread - self.spread_threshold) * 0.1)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        # Short Setup: ETH outpaces BTC by wide margin (spread negative), avoid entering into extreme oversold
        if spread <= -self.spread_threshold and rsi > 32.0:
            confidence = min(0.9, 0.55 + max(0.0, abs(spread) - self.spread_threshold) * 0.1)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion_short",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        return None