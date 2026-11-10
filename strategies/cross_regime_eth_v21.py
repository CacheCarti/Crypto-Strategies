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
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 8

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(30)
        if len(closes) < 20:
            return None

        # Manage existing position with time-based exit to keep trades independent
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown after an exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset relative performance spread
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive if ETH outperforms, negative if ETH lags

        # Price confirmation via RSI on ETH
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Filter out extreme crisis regimes to prevent toxic divergence traps
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Setup 1: ETH severely lags BTC, but RSI not in free-fall -> Expect catch-up bounce
        if spread <= -2.5 and rsi < 46.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lag_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                }
            )

        # Setup 2: ETH severely outruns BTC, and RSI elevated -> Expect spread mean-reversion
        if spread >= 2.5 and rsi > 54.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + spread / 10.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrun_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                }
            )

        return None