from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetCatchup(Strategy):
    METADATA = {
        "name": "CrossAssetCatchup",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.4
        self.zscore_lookback = 30
        self.spread_history = []

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

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        if len(closes) < 40:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret
        self.spread_history.append(spread)

        if len(self.spread_history) > 100:
            self.spread_history.pop(0)

        # Respect cooldown after any trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check existing position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos = ctx.position
            entry_p = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close
            ret_bps = ((ctx.bar.close - entry_p) / entry_p) * 10000.0 if pos_dir == "long" else ((entry_p - ctx.bar.close) / entry_p) * 10000.0
            
            # Dynamic exit on spread reversion to parity
            if pos_dir == "long" and spread >= 0.2:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_catchup_completed", "spread": spread, "ret_bps": ret_bps})
            elif pos_dir == "short" and spread <= -0.2:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_mean_reversion_completed", "spread": spread, "ret_bps": ret_bps})
            return None

        if len(self.spread_history) < self.zscore_lookback:
            return None

        spread_z = self._zscore(self.spread_history, self.zscore_lookback)
        rsi = self._rsi(closes, 14)

        # Regimes where extreme divergence occurs and creates an event boundary
        # Event 1: BTC is strong/firm, but ETH drastically lags (spread < -threshold, spread_z < -1.8)
        if spread <= -self.spread_threshold and spread_z <= -1.8 and btc_ret > -1.5 and rsi < 52.0:
            confidence = min(0.9, 0.6 + abs(spread_z) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_event",
                    "spread": spread,
                    "spread_z": spread_z,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        # Event 2: ETH is wildly overstretched versus BTC (spread > threshold, spread_z > 1.8)
        if spread >= self.spread_threshold and spread_z >= 1.8 and rsi > 48.0:
            confidence = min(0.9, 0.6 + spread_z * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overrunning_btc_exhaustion_event",
                    "spread": spread,
                    "spread_z": spread_z,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                },
            )

        return None