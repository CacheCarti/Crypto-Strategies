from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_lookback = 24
        self.rsi_period = 14
        self.min_zscore = 1.6

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
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown guard to avoid trade clustering and high friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out extreme volatility regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        
        # Calculate instant spread: positive when ETH leads BTC, negative when ETH lags BTC
        spread = eth_ret - btc_ret

        # Track spread in historical state on the instance
        if not hasattr(self, "_spread_history"):
            self._spread_history = []
        self._spread_history.append(spread)
        if len(self._spread_history) > self.spread_lookback * 2:
            self._spread_history.pop(0)

        spread_z = self._zscore(self._spread_history, self.spread_lookback)
        rsi = self._rsi(closes, self.rsi_period)

        if spread_z is None or rsi is None:
            return None

        pos_dir = ctx.position_direction()

        # Dynamic exit condition for mean reversion
        if pos_dir == "long":
            if spread_z >= 0.2 or rsi >= 62.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_mean_reversion_target", "spread_z": spread_z, "rsi": rsi}
                )
        elif pos_dir == "short":
            if spread_z <= -0.2 or rsi <= 38.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_mean_reversion_target", "spread_z": spread_z, "rsi": rsi}
                )

        # Skip entries if already in a position
        if ctx.has_position():
            return None

        # ETH severely lags BTC -> Expect ETH catch-up rally
        # Require ETH not to be overbought locally (rsi < 48)
        if spread_z <= -self.min_zscore and rsi < 48.0:
            conf = min(0.9, 0.55 + min(abs(spread_z) * 0.1, 0.35))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread_z": spread_z,
                    "rsi": rsi,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                }
            )

        # ETH outruns BTC excessively -> Expect ETH to cool off
        # Require ETH not to be oversold locally (rsi > 52)
        if spread_z >= self.min_zscore and rsi > 52.0:
            conf = min(0.9, 0.55 + min(abs(spread_z) * 0.1, 0.35))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread_z": spread_z,
                    "rsi": rsi,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                }
            )

        return None