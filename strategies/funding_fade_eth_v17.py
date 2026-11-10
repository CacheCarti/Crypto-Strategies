from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.cooldown_remaining = 0
        self.cooldown_period = 6
        self.pos_bars = 0
        self.extreme_long_funding = -0.00015
        self.extreme_short_funding = 0.00030
        self.neutral_upper = 0.00010
        self.neutral_lower = -0.00005

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        self.cooldown_remaining = self.cooldown_period
        self.pos_bars = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 20:
            self.funding_history.pop(0)

        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        if len(self.funding_history) >= 3:
            avg_funding_3 = sum(self.funding_history[-3:]) / 3.0
        else:
            avg_funding_3 = funding

        sma_fast = self._sma(closes, 12)
        sma_slow = self._sma(closes, 30)
        rsi = self._rsi(closes, 14)

        if sma_fast is None or sma_slow is None or rsi is None:
            return None

        trend_distance_bps = ((sma_fast - sma_slow) / sma_slow) * 10000.0

        if ctx.has_position():
            self.pos_bars += 1
            direction = ctx.position_direction()

            if direction == "long":
                if avg_funding_3 >= self.neutral_upper or rsi >= 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_or_overbought",
                            "avg_funding_3": avg_funding_3,
                            "rsi": rsi,
                            "pos_bars": self.pos_bars,
                        },
                    )
            elif direction == "short":
                if avg_funding_3 <= self.neutral_lower or rsi <= 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_or_oversold",
                            "avg_funding_3": avg_funding_3,
                            "rsi": rsi,
                            "pos_bars": self.pos_bars,
                        },
                    )
            return None

        if self.cooldown_remaining > 0:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        long_funding_extreme = (
            avg_funding_3 <= self.extreme_long_funding
            and funding <= self.extreme_long_funding * 0.8
        )
        long_filter = trend_distance_bps > -250.0 and rsi < 58.0 and rsi > 28.0

        if long_funding_extreme and long_filter:
            confidence = min(0.85, 0.60 + abs(avg_funding_3) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "negative_funding_squeeze_setup",
                    "avg_funding_3": avg_funding_3,
                    "funding": funding,
                    "rsi": rsi,
                    "trend_distance_bps": trend_distance_bps,
                },
            )

        short_funding_extreme = (
            avg_funding_3 >= self.extreme_short_funding
            and funding >= self.extreme_short_funding * 0.8
        )
        short_filter = trend_distance_bps < 250.0 and rsi > 42.0 and rsi < 72.0

        if short_funding_extreme and short_filter:
            confidence = min(0.85, 0.60 + avg_funding_3 * 800.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "positive_funding_fade_setup",
                    "avg_funding_3": avg_funding_3,
                    "funding": funding,
                    "rsi": rsi,
                    "trend_distance_bps": trend_distance_bps,
                },
            )

        return None