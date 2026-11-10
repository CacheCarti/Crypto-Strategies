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
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.2
        self.max_hold_bars = 16
        self.entry_bar = -999

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        # Check in-flight hold duration safety exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_flat", "bars_held": bars_held}
                )
            return None

        # Enforce post-exit cooldown to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Long Event: BTC leads (btc_ret > 0), ETH heavily lags, RSI not overbought
        if spread <= -self.spread_threshold and btc_ret > 0.4 and rsi < 55.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread": round(spread, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "rsi": round(rsi, 2)
                }
            )

        # Short Event: ETH heavily overextends BTC, RSI elevated, spread mean reversion
        if spread >= self.spread_threshold and rsi > 50.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.5 + spread / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrun_reversion_short",
                    "spread": round(spread, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "rsi": round(rsi, 2)
                }
            )

        return None