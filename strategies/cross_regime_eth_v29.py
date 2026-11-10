from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.window = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.z_thresh = 1.65

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
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.window:
            return None

        # Rolling z-score of spread to avoid brittle fixed thresholds
        mean_spread = sum(self.spread_history) / self.window
        var = sum((x - mean_spread) ** 2 for x in self.spread_history) / self.window
        std = math.sqrt(var)
        z = (spread - mean_spread) / std if std > 1e-6 else 0.0

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # If holding a position, look for spread normalization or RSI exhaustion exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (z >= 0.2 or rsi >= 65.0):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_spread_reverted", "z": round(z, 2), "rsi": round(rsi, 1)})
            elif pos_dir == "short" and (z <= -0.2 or rsi <= 35.0):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_spread_reverted", "z": round(z, 2), "rsi": round(rsi, 1)})
            return None

        # Mandatory exit cooldown to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis filter: avoid mean-reversion during structural breakdowns
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.45:
            return None

        # Catch-up setup: ETH significantly lags BTC, but not in terminal freefall
        if z < -self.z_thresh and rsi < 48.0 and rsi > 25.0:
            conf = min(0.9, max(0.55, 0.5 + abs(z) * 0.1))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_mean_reversion",
                    "z_spread": round(z, 2),
                    "eth_ret": round(eth_ret, 2),
                    "btc_ret": round(btc_ret, 2),
                    "rsi": round(rsi, 1),
                },
            )

        # Overextended setup: ETH outruns BTC excessively, ripe for mean-reversion pull-in
        if z > self.z_thresh and rsi > 52.0 and rsi < 75.0:
            conf = min(0.9, max(0.55, 0.5 + abs(z) * 0.1))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "z_spread": round(z, 2),
                    "eth_ret": round(eth_ret, 2),
                    "btc_ret": round(btc_ret, 2),
                    "rsi": round(rsi, 1),
                },
            )

        return None