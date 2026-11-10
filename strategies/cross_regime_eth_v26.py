from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.rsi_period = 14
        self.spread_threshold = 2.2  # Material spread in 24h return percentage points
        self.spread_z_period = 30

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
        closes = ctx.closes(45)
        if len(closes) < 40:
            return None

        # Cooldown guard to avoid overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive if ETH outperforms, negative if ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Manage active position exits / rebalancing
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion target achieved: spread has normalized
            if direction == "long" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_lag_normalized", "spread": spread, "rsi": rsi}
                )
            elif direction == "short" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outrun_normalized", "spread": spread, "rsi": rsi}
                )
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: ETH lags BTC materially (spread < -threshold), confirmed by non-overbought local RSI
        if spread < -self.spread_threshold and rsi < 58.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        # Short Setup: ETH outruns BTC significantly (spread > threshold), confirmed by non-oversold local RSI
        if spread > self.spread_threshold and rsi > 42.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_mean_reversion_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        return None