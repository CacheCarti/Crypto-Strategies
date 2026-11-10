from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.spread_threshold = 1.6
        self.rsi_period = 14

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        # Cooldown guard to avoid churn and friction drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip during severe market disruptions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "crisis_score": crisis_score}
                )
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive when ETH outperforms, negative when ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position management: exit on mean reversion or indicator exhaustion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (spread >= 0.2 or rsi > 62.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and (spread <= -0.2 or rsi < 38.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi}
                )
            return None

        # Selective entries based on spread dislocation + local price exhaustion
        # ETH lagging BTC significantly + RSI confirming local pullback
        if spread <= -self.spread_threshold and rsi < 46.0:
            conf = min(0.9, 0.55 + abs(spread) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        # ETH outrunning BTC by wide margin + RSI confirming local overextension
        if spread >= self.spread_threshold and rsi > 54.0:
            conf = min(0.9, 0.55 + abs(spread) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        return None