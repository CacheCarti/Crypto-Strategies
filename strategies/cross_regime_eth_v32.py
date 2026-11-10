from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.spread_threshold = 2.2
        self.max_spread = 8.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
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

        # Respect cooldown to limit turnover and trade count
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes to prevent breakdown whipsaws
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, 14)

        # Manage open position exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread >= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_normalized_long_exit", "spread": spread, "rsi": rsi})
            elif pos_dir == "short" and spread <= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_normalized_short_exit", "spread": spread, "rsi": rsi})
            return None

        # Entry logic: ETH materially lagging BTC -> Long ETH (catch-up)
        if spread <= -self.spread_threshold and spread >= -self.max_spread and rsi < 58.0:
            conf = min(0.85, 0.55 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        # Entry logic: ETH significantly outrunning BTC -> Short ETH (mean-reversion)
        if spread >= self.spread_threshold and spread <= self.max_spread and rsi > 42.0:
            conf = min(0.85, 0.55 + spread * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi
                }
            )

        return None