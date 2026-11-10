from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Spread Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.4
        self.bars_in_position = 0

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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Manage active position
        if ctx.has_position():
            self.bars_in_position += 1
            if self.bars_in_position >= 16:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_hold_time_reached", "bars_held": self.bars_in_position})
            return None

        # Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset spread signal (24h return diff)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Avoid entries during systemic crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Long Catch-up: ETH materially lags BTC, but ETH is not structurally collapsing (RSI not completely broken)
        if spread <= -self.spread_threshold and rsi < 46.0 and btc_ret > -3.0:
            confidence = min(0.90, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                },
            )

        # Short Overextension: ETH overextended vs BTC, ETH RSI confirming local exhaustion
        if spread >= self.spread_threshold and rsi > 58.0:
            confidence = min(0.90, 0.55 + spread * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                },
            )

        return None