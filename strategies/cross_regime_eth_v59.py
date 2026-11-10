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
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.cooldown_bars = 8
        self.last_trade_bar = -100
        self.spread_history = []

    def _zscore(self, spread_val: float, history: list) -> Optional[float]:
        if len(history) < 20:
            return None
        recent = history[-self.period:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = math.sqrt(variance)
        if std < 1e-6:
            return 0.0
        return (spread_val - mean) / std

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.period + 15)
        if len(closes) < self.period + 1:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        raw_spread = eth_ret - btc_ret
        self.spread_history.append(raw_spread)
        if len(self.spread_history) > 200:
            self.spread_history.pop(0)

        z_spread = self._zscore(raw_spread, self.spread_history)
        if z_spread is None:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        bars_since_trade = ctx.bar_index - self.last_trade_bar
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Regime filter: avoid taking counter-trend spreads during extreme systemic crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "CRISIS" or regime == "MELTDOWN":
            if has_pos:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Position management / Exit rules
        if has_pos:
            if pos_dir == "long" and z_spread >= 0.2:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_long_exit", "z_spread": round(z_spread, 3), "rsi": round(rsi, 1)}
                )
            elif pos_dir == "short" and z_spread <= -0.2:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_short_exit", "z_spread": round(z_spread, 3), "rsi": round(rsi, 1)}
                )
            return None

        # Entry gating
        if bars_since_trade < self.cooldown_bars:
            return None

        # Statistical spread divergence: ETH lagging BTC materially (z <= -1.8) with price not severely overbought
        if z_spread <= -1.8 and rsi < 58.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, max(0.55, 0.55 + abs(z_spread) * 0.1))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "z_spread": round(z_spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 1),
                }
            )

        # Statistical spread divergence: ETH outrunning BTC materially (z >= 1.8) with price not severely oversold
        if z_spread >= 1.8 and rsi > 42.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, max(0.55, 0.55 + abs(z_spread) * 0.1))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrunning_btc_reversion_short",
                    "z_spread": round(z_spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 1),
                }
            )

        return None