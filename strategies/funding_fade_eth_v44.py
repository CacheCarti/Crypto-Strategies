from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.max_funding_bars = 5
        self.cooldown_remaining = 0
        self.cooldown_period = 6
        self.bars_in_trade = 0
        self.max_hold_bars = 24
        self.pos_direction = None

        self.funding_threshold_high = 0.00025
        self.funding_threshold_low = -0.00025
        self.funding_norm_threshold = 0.00008

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
        self.pos_direction = None
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_funding_bars:
            self.funding_history.pop(0)

        price = closes[-1]
        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)

        if sma30 is None or rsi14 is None:
            return None

        has_pos = ctx.has_position()

        if has_pos:
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            norm_exit = False
            if direction == "long" and current_funding > -self.funding_norm_threshold:
                norm_exit = True
            elif direction == "short" and current_funding < self.funding_norm_threshold:
                norm_exit = True

            time_exit = self.bars_in_trade >= self.max_hold_bars

            if norm_exit or time_exit:
                self.pos_direction = None
                self.bars_in_trade = 0
                self.cooldown_remaining = self.cooldown_period
                reason = "funding_normalized" if norm_exit else "time_limit_reached"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "funding": current_funding,
                        "bars_held": self.bars_in_trade,
                        "rsi": rsi14,
                        "price": price,
                    },
                )
            return None

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            return None

        if len(self.funding_history) < self.max_funding_bars:
            return None

        recent_funding = self.funding_history[-3:]
        persistent_negative = all(f <= self.funding_threshold_low for f in recent_funding)
        persistent_positive = all(f >= self.funding_threshold_high for f in recent_funding)

        pct_dist_from_sma = (price - sma30) / sma30

        if persistent_negative and abs(pct_dist_from_sma) <= 0.04 and rsi14 < 48.0:
            confidence = min(0.85, 0.55 + abs(current_funding) * 500.0)
            self.pos_direction = "long"
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "sma30_dist": pct_dist_from_sma,
                    "price": price,
                },
            )

        if persistent_positive and abs(pct_dist_from_sma) <= 0.04 and rsi14 > 52.0:
            confidence = min(0.85, 0.55 + current_funding * 500.0)
            self.pos_direction = "short"
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_exhaustion",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "sma30_dist": pct_dist_from_sma,
                    "price": price,
                },
            )

        return None