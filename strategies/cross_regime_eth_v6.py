from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetMeanReversion(Strategy):
    METADATA = {
        "name": "CrossAssetSpreadReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.spread_entry_threshold = 1.75  # 1.75% divergence threshold
        self.spread_exit_threshold = 0.25   # convergence threshold
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.consecutive_losses = 0

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
        pnl = position.get("return_bps", 0.0)
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        # Filter out extreme panic regimes
        if ctx.regime == "crisis":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "regime": ctx.regime}
                )
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive when ETH outperforms, negative when ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        bars_since_trade = ctx.bar_index - self.last_trade_bar
        current_pos = ctx.position_direction()

        # Exit logic for open positions
        if current_pos == "long":
            # ETH has caught up to or exceeded BTC return, or RSI overbought
            if spread >= -self.spread_exit_threshold or rsi > 68.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_catchup_reached_or_rsi_overbought",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3)
                    }
                )
            return None

        if current_pos == "short":
            # Spread normalized or RSI oversold
            if spread <= self.spread_exit_threshold or rsi < 32.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_mean_reversion_reached_or_rsi_oversold",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3)
                    }
                )
            return None

        # Cooldown guard before new entries
        if bars_since_trade < self.cooldown_bars:
            return None

        # Base confidence adjusted by loss streak
        base_confidence = max(0.5, 0.75 - (0.05 * self.consecutive_losses))

        # Long Setup: ETH is significantly lagging BTC, but not completely broken in RSI
        if spread <= -self.spread_entry_threshold and rsi < 58.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=base_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_opportunity",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "regime": ctx.regime
                }
            )

        # Short Setup: ETH has aggressively overextended vs BTC
        if spread >= self.spread_entry_threshold and rsi > 42.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=base_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion_opportunity",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "regime": ctx.regime
                }
            )

        return None