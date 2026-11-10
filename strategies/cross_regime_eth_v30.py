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
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.2
        self.spread_history = []
        self.max_hist = 72

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.max_hist:
            self.spread_history.pop(0)

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", ctx.bar.close)

            # Reversion exit triggers
            if pos_dir == "long" and spread >= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "price": ctx.bar.close}
                )
            elif pos_dir == "short" and spread <= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Require a brief window to confirm a statistical boundary
        if len(self.spread_history) < 24:
            return None

        mean_spread = sum(self.spread_history) / len(self.spread_history)
        var_spread = sum((x - mean_spread) ** 2 for x in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(var_spread) if var_spread > 1e-6 else 1.0
        z_spread = (spread - mean_spread) / std_spread

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Crisis / high-vol regime filter to avoid falling knives
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: ETH significantly lagging BTC (spread <= -threshold & z <= -1.8) + RSI oversold confirmation
        if spread <= -self.spread_threshold and z_spread <= -1.8 and rsi < 42.0:
            confidence = min(0.9, max(0.55, abs(z_spread) / 3.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "z_spread": round(z_spread, 2),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2)
                }
            )

        # Short Setup: ETH significantly outrunning BTC (spread >= threshold & z >= 1.8) + RSI overbought confirmation
        if spread >= self.spread_threshold and z_spread >= 1.8 and rsi > 58.0:
            confidence = min(0.9, max(0.55, z_spread / 3.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_revert",
                    "spread": round(spread, 3),
                    "z_spread": round(z_spread, 2),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2)
                }
            )

        return None