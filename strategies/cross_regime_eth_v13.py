from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.history_len = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_threshold = 1.6
        self.rsi_period = 14

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track 24h rolling return spread: ETH minus BTC
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.history_len:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.history_len:
            return None

        # Compute z-score of the spread
        mean_spread = sum(self.spread_history) / float(self.history_len)
        variance = sum((x - mean_spread) ** 2 for x in self.spread_history) / float(self.history_len)
        std_spread = math.sqrt(variance)

        if std_spread < 1e-5:
            return None

        z_score = (spread - mean_spread) / std_spread
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Check existing position for early mean-reversion take-profit / cut
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and z_score > 0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "z_score": round(z_score, 2), "rsi": round(rsi, 1)}
                )
            elif pos_dir == "short" and z_score < -0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "z_score": round(z_score, 2), "rsi": round(rsi, 1)}
                )
            return None

        # Strict post-exit cooldown to avoid churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme meltdown conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long Setup: ETH significantly lagging BTC (z_score <= -1.6) and ETH not overbought
        if z_score <= -self.entry_threshold and rsi < 58.0:
            confidence = min(0.9, 0.55 + 0.15 * min(abs(z_score) - self.entry_threshold, 2.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lags_btc_spread_oversold",
                    "spread": round(spread, 3),
                    "z_score": round(z_score, 2),
                    "rsi": round(rsi, 1),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2)
                }
            )

        # Short Setup: ETH outrunning BTC excessively (z_score >= 1.6) and ETH not oversold
        if z_score >= self.entry_threshold and rsi > 42.0:
            confidence = min(0.9, 0.55 + 0.15 * min(z_score - self.entry_threshold, 2.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outruns_btc_spread_overbought",
                    "spread": round(spread, 3),
                    "z_score": round(z_score, 2),
                    "rsi": round(rsi, 1),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2)
                }
            )

        return None