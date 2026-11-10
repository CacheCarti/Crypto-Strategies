from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 72,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_window = 48
        self.spread_history = []

    def _zscore(self, values: list) -> float:
        if len(values) < 20:
            return 0.0
        n = len(values)
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        std = math.sqrt(variance)
        if std < 1e-6:
            return 0.0
        return (values[-1] - mean) / std

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and friction penalty
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.spread_window:
            self.spread_history.pop(0)

        if len(self.spread_history) < 24:
            return None

        z = self._zscore(self.spread_history)
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions: spread reverting back to mean
        if has_pos:
            if pos_dir == "long" and z >= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_to_mean_long_exit",
                    "zscore": round(z, 2),
                    "rsi": round(rsi, 2),
                    "spread": round(spread, 3)
                })
            elif pos_dir == "short" and z <= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_to_mean_short_exit",
                    "zscore": round(z, 2),
                    "rsi": round(rsi, 2),
                    "spread": round(spread, 3)
                })
            return None

        # Market stress filter from Market Context
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # ETH lagging BTC significantly: Z <= -1.8, confirm with non-overbought RSI
        if z <= -1.8 and rsi < 55.0:
            confidence = min(0.9, 0.55 + abs(z) * 0.1)
            return ctx.signal("long", confidence=confidence,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              metadata={
                                  "reason": "eth_lagging_btc_mean_reversion",
                                  "zscore": round(z, 2),
                                  "rsi": round(rsi, 2),
                                  "spread": round(spread, 3),
                                  "btc_ret": round(btc_ret, 3),
                                  "eth_ret": round(eth_ret, 3)
                              })

        # ETH outrunning BTC significantly: Z >= 1.8, confirm with non-oversold RSI
        if z >= 1.8 and rsi > 45.0:
            confidence = min(0.9, 0.55 + abs(z) * 0.1)
            return ctx.signal("short", confidence=confidence,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              metadata={
                                  "reason": "eth_outrunning_btc_mean_reversion",
                                  "zscore": round(z, 2),
                                  "rsi": round(rsi, 2),
                                  "spread": round(spread, 3),
                                  "btc_ret": round(btc_ret, 3),
                                  "eth_ret": round(eth_ret, 3)
                              })

        return None