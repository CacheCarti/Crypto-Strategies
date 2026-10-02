from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EthBtcSpreadCatchup(Strategy):
    METADATA = {
        "name": "ETH BTC Spread Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.spread_history = []
        self.spread_z_entry = 2.15

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

    def _zscore(self, values, period):
        if len(values) < period:
            return 0.0
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std < 1e-6:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + self.rsi_period + 10)
        if len(closes) < self.period + self.rsi_period:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": round(crisis_score, 3)},
                )
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > 200:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.period:
            return None

        spread_z = self._zscore(self.spread_history, self.period)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position Management / Exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Require meaningful spread reversion or clear indicator exhaustion before manual exit
            if pos_dir == "long" and (spread_z >= 0.5 or rsi >= 70.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_spread_reverted",
                        "spread_z": round(spread_z, 3),
                        "rsi": round(rsi, 2),
                        "spread": round(spread, 4),
                    },
                )
            elif pos_dir == "short" and (spread_z <= -0.5 or rsi <= 30.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_spread_reverted",
                        "spread_z": round(spread_z, 3),
                        "rsi": round(rsi, 2),
                        "spread": round(spread, 4),
                    },
                )
            return None

        # Strict Cooldown Guard to prevent trade churn and over-trading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup: ETH materially lagging BTC (spread z-score <= -2.15) with RSI confirming room to run (< 48)
        if spread_z <= -self.spread_z_entry and rsi < 48.0:
            confidence = min(0.88, 0.65 + (abs(spread_z) - self.spread_z_entry) * 0.12)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_strong_divergence",
                    "spread_z": round(spread_z, 3),
                    "spread": round(spread, 4),
                    "btc_ret": round(btc_ret, 4),
                    "eth_ret": round(eth_ret, 4),
                    "rsi": round(rsi, 2),
                },
            )

        # Short Setup: ETH outrunning BTC (spread z-score >= 2.15) with RSI elevated (> 52)
        if spread_z >= self.spread_z_entry and rsi > 52.0:
            confidence = min(0.88, 0.65 + (abs(spread_z) - self.spread_z_entry) * 0.12)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_strong_reversion",
                    "spread_z": round(spread_z, 3),
                    "spread": round(spread, 4),
                    "btc_ret": round(btc_ret, 4),
                    "eth_ret": round(eth_ret, 4),
                    "rsi": round(rsi, 2),
                },
            )

        return None