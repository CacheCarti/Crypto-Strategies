from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcCatchupReversion(Strategy):
    METADATA = {
        "name": "EthBtcCatchupReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.2  # 2.2% 24h return divergence
        self.max_hold_bars = 10

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset returns (24h rolling return %)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # Positive when ETH lags BTC

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit"})
            return None

        # Exit logic for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion achieved: spread collapsed near zero or flipped
            if pos_dir == "long" and (spread <= 0.2 or rsi > 65.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and (spread >= -0.2 or rsi < 35.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi}
                )
            return None

        # Entry logic
        # ETH lagging BTC: BTC is up or neutral, ETH materially behind, ETH not overbought
        if spread >= self.spread_threshold and rsi < 55.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lags_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi
                }
            )

        # ETH outrunning BTC: ETH materially ahead of BTC, ETH not oversold
        if spread <= -self.spread_threshold and rsi > 45.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outruns_btc_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi
                }
            )

        return None