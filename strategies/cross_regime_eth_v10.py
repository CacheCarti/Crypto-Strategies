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
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.cooldown_period = 8
        self.last_trade_bar = -100

    def _zscore(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Check cooldown window
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_period:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Avoid crisis breakdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, 14)
        zscore = self._zscore(closes, 20)
        if rsi is None or zscore is None:
            return None

        # Event boundary: ETH lag catch-up (BTC leading, ETH lagging significantly + local oversold)
        if spread <= -self.spread_threshold and rsi < 42.0 and zscore < -0.8:
            if not ctx.has_position() or ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_lag_cross_reversion_long",
                        "spread": round(spread, 3),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                        "rsi": round(rsi, 2),
                        "zscore": round(zscore, 2),
                    },
                )

        # Event boundary: ETH overextension (ETH outrunning BTC by large spread + local overbought)
        elif spread >= self.spread_threshold and rsi > 58.0 and zscore > 0.8:
            if not ctx.has_position() or ctx.position_direction() == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_outrun_cross_reversion_short",
                        "spread": round(spread, 3),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                        "rsi": round(rsi, 2),
                        "zscore": round(zscore, 2),
                    },
                )

        # Discretionary mean-reversion exit when spread neutralizes
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread >= 0.0 and rsi >= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_spread_neutralized",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                    },
                )
            elif direction == "short" and spread <= 0.0 and rsi <= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_spread_neutralized",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                    },
                )

        return None