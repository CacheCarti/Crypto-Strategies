from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_thresh = -0.00015
        self.funding_short_thresh = 0.00035
        self.funding_history = []
        self.cooldown_remaining = 0
        self.cooldown_period = 6
        self.bars_in_trade = 0
        self.max_hold_bars = 24

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        self.cooldown_remaining = self.cooldown_period
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        ema34 = self._ema(closes, 34)
        rsi14 = self._rsi(closes, 14)
        if ema34 is None or rsi14 is None:
            return None

        price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")

        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()

            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": self.bars_in_trade}
                )

            if pos_dir == "long" and current_funding >= 0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_long_exit", "funding": current_funding, "rsi": rsi14}
                )
            elif pos_dir == "short" and current_funding <= 0.00010:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_short_exit", "funding": current_funding, "rsi": rsi14}
                )
            return None

        if self.cooldown_remaining > 0:
            return None

        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.funding_history) >= 3:
            avg_recent_funding = sum(self.funding_history[-3:]) / 3.0
        else:
            avg_recent_funding = current_funding

        # Squeeze short crowd: funding heavily negative, persistent across bars, no deep freefall
        if avg_recent_funding < self.funding_long_thresh and current_funding < self.funding_long_thresh:
            if price > ema34 * 0.965 and rsi14 > 32.0 and rsi14 < 65.0:
                confidence = min(0.9, 0.65 + abs(avg_recent_funding) * 800.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_negative_funding_squeeze",
                        "funding": current_funding,
                        "avg_funding_3b": avg_recent_funding,
                        "rsi14": rsi14,
                        "ema34": ema34,
                        "price": price,
                    }
                )

        # Fade over-leveraged longs: funding elevated, persistent, not breaking violently upward
        if avg_recent_funding > self.funding_short_thresh and current_funding > self.funding_short_thresh:
            if price < ema34 * 1.035 and rsi14 < 68.0 and rsi14 > 35.0:
                confidence = min(0.9, 0.65 + avg_recent_funding * 600.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_positive_funding_exhaustion",
                        "funding": current_funding,
                        "avg_funding_3b": avg_recent_funding,
                        "rsi14": rsi14,
                        "ema34": ema34,
                        "price": price,
                    }
                )

        return None