from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EthBtcRelativeValueStrategy(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Value Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.spread_window = 24
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.spread_history = []

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def _zscore(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std < 1e-6:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret
        self.spread_history.append(spread)

        if len(self.spread_history) < self.spread_window:
            return None

        # Keep buffer bounded
        if len(self.spread_history) > self.spread_window * 3:
            self.spread_history = self.spread_history[-self.spread_window * 2 :]

        z_spread = self._zscore(self.spread_history, self.spread_window)
        rsi = self._rsi(closes, self.rsi_period)

        if z_spread is None or rsi is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions: Reversion back towards equilibrium
        if has_pos:
            if pos_dir == "long" and (z_spread >= 0.4 or spread >= 0.5):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_lag_reverted_to_mean",
                        "spread": round(spread, 3),
                        "z_spread": round(z_spread, 2),
                        "rsi": round(rsi, 2),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                    },
                )
            elif pos_dir == "short" and (z_spread <= -0.4 or spread <= -0.5):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_premium_reverted_to_mean",
                        "spread": round(spread, 3),
                        "z_spread": round(z_spread, 2),
                        "rsi": round(rsi, 2),
                        "btc_ret": round(btc_ret, 3),
                        "eth_ret": round(eth_ret, 3),
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: ETH lagging BTC materially -> Long ETH catch-up
        if z_spread < -1.65 and spread < -1.2 and rsi > 30.0 and rsi < 65.0:
            conf = min(0.9, 0.55 + abs(z_spread) * 0.1)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_entry",
                    "spread": round(spread, 3),
                    "z_spread": round(z_spread, 2),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                },
            )

        # Entry logic: ETH outrunning BTC by wide margin -> Short ETH mean reversion
        if z_spread > 1.65 and spread > 1.2 and rsi > 38.0 and rsi < 72.0:
            conf = min(0.9, 0.55 + abs(z_spread) * 0.1)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion_entry",
                    "spread": round(spread, 3),
                    "z_spread": round(z_spread, 2),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                },
            )

        return None