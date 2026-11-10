from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeSpreadReversion(Strategy):
    METADATA = {
        "name": "EthBtcRelativeSpreadReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_window = 48
        self.cooldown_period = 8
        self.last_trade_bar = -999
        self.spread_history = []

    def _stddev(self, values):
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(var)

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cross-asset spread calculation: eth_return_pct minus btc_return_pct
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        current_spread = eth_ret - btc_ret

        self.spread_history.append(current_spread)
        if len(self.spread_history) > self.spread_window:
            self.spread_history.pop(0)

        # Require sufficient history for statistical spread evaluation
        if len(self.spread_history) < self.spread_window:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Standardize the spread over recent rolling distribution
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        std_spread = self._stddev(self.spread_history)
        if std_spread < 1e-6:
            return None

        spread_z = (current_spread - mean_spread) / std_spread

        # Confirmation oscillator on ETH
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of spread back towards neutral
            if pos_dir == "long" and spread_z >= 0.2:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_catchup_normalized", "spread_z": spread_z, "rsi": rsi}
                )
            elif pos_dir == "short" and spread_z <= -0.2:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outperformance_cooled", "spread_z": spread_z, "rsi": rsi}
                )
            return None

        # Entry logic:
        # Long: ETH significantly lagging BTC (spread_z <= -1.8) and RSI not overheated
        if spread_z <= -1.8 and rsi < 55.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(spread_z) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_setup",
                    "spread_z": round(spread_z, 3),
                    "current_spread": round(current_spread, 3),
                    "rsi": round(rsi, 2),
                    "eth_ret": round(eth_ret, 2),
                    "btc_ret": round(btc_ret, 2),
                }
            )

        # Short: ETH significantly outrunning BTC (spread_z >= 1.8) and RSI showing exhaustion
        if spread_z >= 1.8 and rsi > 45.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(spread_z) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion_setup",
                    "spread_z": round(spread_z, 3),
                    "current_spread": round(current_spread, 3),
                    "rsi": round(rsi, 2),
                    "eth_ret": round(eth_ret, 2),
                    "btc_ret": round(btc_ret, 2),
                }
            )

        return None