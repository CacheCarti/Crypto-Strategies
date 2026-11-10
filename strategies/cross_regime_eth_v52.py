from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.spread_threshold = 1.8
        self.rsi_period = 14

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
        if len(closes) < 30:
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Robust regime filter: avoid extreme crisis volatility where cross-asset beta breaks
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_risk", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        # Calculate spread: btc_return_pct - eth_return_pct
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # Positive when ETH lags BTC materially

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion profit lock / signal neutralization
            if pos_dir == "long" and (spread < 0.2 or rsi > 65.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and (spread > -0.2 or rsi < 35.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "rsi": rsi}
                )
            return None

        # Selective entries: Spread divergence confirmed by technical price momentum
        # Long ETH: BTC is outperforming ETH by > 1.8%, and ETH is not overbought (RSI < 52)
        if spread > self.spread_threshold and rsi < 52.0:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "regime": regime
                }
            )

        # Short ETH: ETH has surged beyond BTC by > 1.8%, and ETH is not deeply oversold (RSI > 48)
        if spread < -self.spread_threshold and rsi > 48.0:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "regime": regime
                }
            )

        return None