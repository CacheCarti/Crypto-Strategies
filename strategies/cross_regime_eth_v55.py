from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeValueReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Catchup Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.5      # Return divergence threshold in %
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -100

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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        # Cooldown guard to avoid overtrading and friction drag
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Skip during severe market meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # > 0 means ETH outperforming, < 0 means ETH lagging

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Manage existing positions: exit when spread mean-reverts or momentum neutralizes
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (spread > -0.5 or rsi > 55.0):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi})
            elif pos_dir == "short" and (spread < 0.5 or rsi < 45.0):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi})
            return None

        # Long Setup: ETH significantly lagging BTC while local ETH RSI is not in overbought territory
        # Ensures ETH catch-up trade has high conviction and avoids chasing overbought spikes
        if spread <= -self.spread_threshold and rsi < 48.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={"reason": "eth_lagging_btc_catchup", "spread": spread, "rsi": rsi, "btc_ret": btc_ret, "eth_ret": eth_ret}
            )

        # Short Setup: ETH stretched far ahead of BTC while local ETH RSI indicates buyer exhaustion
        if spread >= self.spread_threshold and rsi > 52.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={"reason": "eth_extended_btc_reversion", "spread": spread, "rsi": rsi, "btc_ret": btc_ret, "eth_ret": eth_ret}
            )

        return None