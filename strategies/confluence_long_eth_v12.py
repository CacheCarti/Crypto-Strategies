from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversal(Strategy):
    METADATA = {
        "name": "EthConfluenceReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Manage existing open long position
        if ctx.has_position():
            # Exit when technicals recover significantly or normalize
            if rsi >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_target_hit", "rsi": round(rsi, 2)}
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Triple edge signals
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Entry confluence: negative/flat funding (crowd short/neutral), fear regime, and oversold bounce
        is_funding_edge = funding_rate <= 0.0
        is_sentiment_edge = fear_greed <= 38.0
        is_rsi_edge = rsi <= 35.0

        if is_funding_edge and is_sentiment_edge and is_rsi_edge:
            # Scaled conviction based on severity of oversold & sentiment
            confidence = 0.65
            if rsi < 28.0:
                confidence += 0.15
            if funding_rate < -0.0001:
                confidence += 0.10
            confidence = min(1.0, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding_rate, 6),
                    "fear_greed": round(fear_greed, 1),
                    "close": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index