from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.4
        self.rsi_period = 14

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

        # Check for mandatory cooldown after last position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown environments where correlations decouple destructively
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime}
                )
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Active trade management: exit if spread reverts back towards parity
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (spread >= -0.2 or rsi >= 60.0):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "spread_normalized_long_exit",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2)
                    }
                )
            elif pos_dir == "short" and (spread <= 0.2 or rsi <= 40.0):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "spread_normalized_short_exit",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2)
                    }
                )
            return None

        # Entry logic: ETH materially lagging BTC -> expect catch-up bounce
        # BTC must have positive momentum while ETH is lagging and locally oversold
        if spread <= -self.spread_threshold and btc_ret > 0.3 and rsi < 42.0:
            confidence = min(0.9, 0.6 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2)
                }
            )

        # Entry logic: ETH significantly outrunning BTC -> expect mean reversion
        # Spread stretched high while ETH is locally overextended on RSI
        if spread >= self.spread_threshold and rsi > 58.0:
            confidence = min(0.9, 0.6 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2)
                }
            )

        return None