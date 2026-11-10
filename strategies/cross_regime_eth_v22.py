from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.min_spread_threshold = 2.2  # Material divergence requirement in %
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
        closes = ctx.closes(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: must wait after closing an existing trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence failure analysis:
        # Avoid high-volatility/crisis breakdown where cross-asset correlations uncouple destructively
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "crisis_score": crisis_score, "regime": regime}
                )
            return None

        # 24h Return divergence
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive when ETH outruns, negative when ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reversion exit condition: spread has converged
            if pos_dir == "long" and spread >= -0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and spread <= 0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi}
                )
            return None

        # Entry logic: selective high-conviction spread divergence with RSI confirmation
        # Long catch-up: ETH materially lags BTC, and ETH is not overbought (RSI < 52)
        if spread < -self.min_spread_threshold and rsi < 52.0:
            confidence = min(1.0, 0.6 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "rsi": rsi,
                }
            )

        # Short mean-reversion: ETH outran BTC materially, and ETH is not oversold (RSI > 48)
        if spread > self.min_spread_threshold and rsi > 48.0:
            confidence = min(1.0, 0.6 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "rsi": rsi,
                }
            )

        return None