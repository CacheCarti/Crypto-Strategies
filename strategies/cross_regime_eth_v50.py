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
        self.spread_history = []
        self.history_len = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.spread_z_threshold = 2.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.history_len:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.history_len:
            return None

        mean_spread = sum(self.spread_history) / self.history_len
        var_spread = sum((x - mean_spread) ** 2 for x in self.spread_history) / self.history_len
        std_spread = math.sqrt(var_spread)

        if std_spread < 1e-5:
            return None

        z_score = (spread - mean_spread) / std_spread
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or regime == "MELTDOWN":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "risk_off_crisis_exit", "crisis_score": crisis_score, "regime": regime}
                )
            return None

        current_pos = ctx.position_direction()

        # Exit logic
        if current_pos == "long":
            if z_score >= 0.0 or rsi > 65.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_normalized_long_exit", "z_score": round(z_score, 2), "rsi": round(rsi, 2)}
                )
        elif current_pos == "short":
            if z_score <= 0.0 or rsi < 35.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_normalized_short_exit", "z_score": round(z_score, 2), "rsi": round(rsi, 2)}
                )

        if ctx.has_position():
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Strict boundary events for entry
        # Long ETH: ETH significantly lagging BTC (z_score <= -2.0) and RSI oversold bounce setup
        if z_score <= -self.spread_z_threshold and rsi < 42.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.5 + abs(z_score) * 0.15),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "z_score": round(z_score, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3)
                }
            )

        # Short ETH: ETH outrunning BTC by wide margin (z_score >= 2.0) and RSI overbought setup
        if z_score >= self.spread_z_threshold and rsi > 58.0:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.5 + abs(z_score) * 0.15),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "z_score": round(z_score, 2),
                    "spread": round(spread, 3),
                    "rsi": round(rsi, 2),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index