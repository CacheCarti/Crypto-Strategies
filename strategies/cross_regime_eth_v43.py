from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadCatchUp(Strategy):
    METADATA = {
        "name": "EthBtcSpreadCatchUp",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.rsi_period = 14
        self.spread_threshold = 2.0  # 2.0% divergence threshold between 24h returns
        self.max_hold_bars = 16

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

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset returns (24h return percentage)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive means ETH outrunning BTC, negative means ETH lagging

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Filter out extreme crisis regimes where correlations break down entirely
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": regime})
            return None

        pos_dir = ctx.position_direction()

        # Manage open position
        if ctx.has_position():
            # Mean reversion achieved: spread normalizes or RSI crosses median
            if pos_dir == "long":
                if spread >= -0.2 or rsi >= 62.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long", "spread": spread, "rsi": rsi})
            elif pos_dir == "short":
                if spread <= 0.2 or rsi <= 38.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short", "spread": spread, "rsi": rsi})
            return None

        # Entry logic: ETH lagging BTC (Long ETH catch-up)
        # BTC is performing well or stable (btc_ret > -1.0%), but ETH is lagging behind BTC significantly.
        # RSI confirmation: ETH is not already overbought.
        if spread <= -self.spread_threshold and btc_ret > -1.0 and rsi < 48.0:
            confidence = min(0.85, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        # Entry logic: ETH outrunning BTC (Short ETH mean-reversion)
        # ETH has surged way ahead of BTC, confirmed by elevated local momentum.
        if spread >= self.spread_threshold and btc_ret < 3.0 and rsi > 58.0:
            confidence = min(0.85, 0.55 + spread * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        return None