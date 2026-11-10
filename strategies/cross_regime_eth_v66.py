from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.2
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

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "risk_off_exit", "regime": regime})
            return None

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread >= 0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long", "spread": spread, "rsi": rsi})
            if pos_dir == "short" and spread <= -0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short", "spread": spread, "rsi": rsi})
            return None

        if spread < -self.spread_threshold and rsi < 42.0:
            conf = min(0.9, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "eth_lagging_catchup_mean_reversion", "spread": spread, "btc_ret": btc_ret, "eth_ret": eth_ret, "rsi": rsi}
            )

        if spread > self.spread_threshold and rsi > 58.0:
            conf = min(0.9, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": "eth_outrunning_btc_mean_reversion", "spread": spread, "btc_ret": btc_ret, "eth_ret": eth_ret, "rsi": rsi}
            )

        return None