from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthTripleConfluenceSwing(Strategy):
    METADATA = {
        "name": "ETH Triple Confluence Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_entry_thresh = 36.0
        self.rsi_exit_thresh = 58.0
        self.fg_thresh = 38.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _rsi(self, closes, period: int = 12) -> Optional[float]:
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
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. Manage Active Position (Exit Logic)
        if ctx.has_position():
            # Mean reversion achieved or momentum normalized
            if rsi >= self.rsi_exit_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_target_reached",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "fear_greed": fg_index
                    }
                )

            # Defensive exit if funding flips aggressively positive while sentiment is overheated
            if funding > 0.0003 and rsi > 52.0:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "crowd_long_overheated_exit",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "fear_greed": fg_index
                    }
                )
            return None

        # 2. Check Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. Triple Confluence Long Entry Condition
        # Condition A: Market Sentiment in Fear / Extreme Fear
        sentiment_ok = fg_index <= self.fg_thresh
        # Condition B: Perp Market Crowded Short or Neutral-to-Negative
        funding_ok = funding <= 0.00002
        # Condition C: Price Action Momentum Oversold
        rsi_ok = rsi <= self.rsi_entry_thresh
        # Filter: Avoid entering in chaotic breakdown crisis
        regime_ok = crisis_score < 0.85

        if sentiment_ok and funding_ok and rsi_ok and regime_ok:
            # Scaled confidence based on depth of oversold & sentiment extremity
            base_conf = 0.65
            if fg_index < 25.0:
                base_conf += 0.10
            if rsi < 30.0:
                base_conf += 0.10
            if funding < -0.00005:
                base_conf += 0.10
            confidence = min(0.95, base_conf)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_fear_funding_rsi_oversold",
                    "rsi": round(rsi, 2),
                    "fear_greed": fg_index,
                    "funding_rate": funding,
                    "crisis_score": round(crisis_score, 3)
                }
            )

        return None