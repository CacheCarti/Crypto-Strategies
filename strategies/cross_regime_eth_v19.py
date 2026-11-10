from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 72,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 48          # Rolling window for spread stats
        self.cooldown_period = 8  # Bars to wait after exit
        self.last_exit_bar = -100
        self.spread_history = []

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

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

        # Calculate current return spread: ETH 24h return minus BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.window:
            return None

        # Compute dynamic rolling Z-score of spread to avoid rigid magic numbers
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        std_spread = self._stddev(self.spread_history)
        zscore = (spread - mean_spread) / std_spread if std_spread > 1e-6 else 0.0

        # Technical confirmation indicator
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Check existing position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion completion exit
            if direction == "long" and zscore >= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_mean_long_exit",
                    "zscore": round(zscore, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2)
                })
            elif direction == "short" and zscore <= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_mean_short_exit",
                    "zscore": round(zscore, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2)
                })
            return None

        # Enforce strict cooldown period after positions close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Regime filter: avoid taking counter-trend spreads during market-wide meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long ETH setup: ETH substantially lagging BTC (zscore < -1.8) and ETH not overbought
        if zscore < -1.8 and rsi < 55.0:
            confidence = min(1.0, 0.65 + abs(zscore) * 0.1)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "zscore": round(zscore, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3)
                }
            )

        # Short ETH setup: ETH overrunning BTC (zscore > 1.8) and ETH not oversold
        if zscore > 1.8 and rsi > 45.0:
            confidence = min(1.0, 0.65 + abs(zscore) * 0.1)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrunning_btc_reversion_short",
                    "zscore": round(zscore, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3)
                }
            )

        return None