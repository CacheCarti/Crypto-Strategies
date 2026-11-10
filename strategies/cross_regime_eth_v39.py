from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_window = 36
        self.cooldown_bars = 5
        self.last_exit_bar = -999
        self.spread_history = []

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(max(variance, 1e-8))

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
        closes = ctx.closes(45)
        if len(closes) < 40:
            return None

        # Calculate current return spread: ETH 24h return minus BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.spread_window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_window:
            return None

        # Rolling Z-score of spread to avoid rigid magic numbers
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        std_spread = self._stddev(self.spread_history)
        if std_spread < 0.05:
            spread_z = 0.0
        else:
            spread_z = (spread - mean_spread) / std_spread

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion completion exit: spread reverted near normal
            if direction == "long" and spread_z >= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_spread_reverted", "spread_z": round(spread_z, 2), "rsi": round(rsi, 1)}
                )
            if direction == "short" and spread_z <= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_spread_reverted", "spread_z": round(spread_z, 2), "rsi": round(rsi, 1)}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Setup 1: ETH severely lagging BTC (catch-up long)
        # Spread is significantly negative (z < -1.8) and ETH is not severely overbought (rsi < 60)
        if spread_z <= -1.8 and rsi < 60.0:
            confidence = min(0.85, 0.55 + abs(spread_z) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread_z": round(spread_z, 2),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 1),
                }
            )

        # Setup 2: ETH severely outrunning BTC (mean-reversion short)
        # Spread is significantly positive (z > 1.8) and ETH is not severely oversold (rsi > 40)
        if spread_z >= 1.8 and rsi > 40.0:
            confidence = min(0.85, 0.55 + abs(spread_z) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_btc_fade",
                    "spread_z": round(spread_z, 2),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 1),
                }
            )

        return None