from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math


class EthFundingSqueezeContrarian(Strategy):
    METADATA = {
        "name": "ETH Funding Squeeze Contrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = deque(maxlen=6)
        self.cooldown_bars = 5
        self.last_exit_bar = -999
        self.pos_entry_bar = -999

        # Funding thresholds
        self.funding_neg_thresh = -0.00008  # -0.008% per 8h (shorts paying)
        self.funding_pos_thresh = 0.00025   # +0.025% per 8h (longs paying heavily)
        self.persist_bars = 2

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(40)
        if len(closes) < 20:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Check existing position management & normalization exit
        if ctx.has_position():
            direction = ctx.position_direction()

            # Long exit: crowd is no longer short; funding normalized back to neutral/positive
            if direction == "long" and funding >= 0.00008:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_normalized_long_exit",
                        "funding": funding,
                        "rsi": rsi,
                        "bars_held": ctx.bar_index - self.pos_entry_bar,
                    },
                )

            # Short exit: crowd is no longer long; funding normalized back to low/neutral
            if direction == "short" and funding <= 0.00002:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_normalized_short_exit",
                        "funding": funding,
                        "rsi": rsi,
                        "bars_held": ctx.bar_index - self.pos_entry_bar,
                    },
                )

            return None

        # Cooldown guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.funding_history) < self.persist_bars:
            return None

        recent_funding = list(self.funding_history)[-self.persist_bars :]

        # Squeeze Setup (Long): Crowd is persistently short and paying funding
        # Price filter: RSI must not be completely crashing (RSI > 28) or overbought (RSI < 65)
        persistent_negative = all(f <= self.funding_neg_thresh for f in recent_funding)
        if persistent_negative and 28.0 < rsi < 65.0:
            self.pos_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "funding": funding,
                    "avg_funding_window": sum(recent_funding) / len(recent_funding),
                    "rsi": rsi,
                    "close_price": ctx.bar.close,
                },
            )

        # Fade Setup (Short): Crowd is persistently over-leveraged long and paying high funding
        # Price filter: RSI must not be oversold (RSI > 35) or in runaway euphoria (RSI < 75)
        persistent_positive = all(f >= self.funding_pos_thresh for f in recent_funding)
        if persistent_positive and 35.0 < rsi < 75.0:
            self.pos_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 400.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_fade",
                    "funding": funding,
                    "avg_funding_window": sum(recent_funding) / len(recent_funding),
                    "rsi": rsi,
                    "close_price": ctx.bar.close,
                },
            )

        return None