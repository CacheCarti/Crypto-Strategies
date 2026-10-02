from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
from collections import deque


class FundingRateContrarian(Strategy):
    METADATA = {
        "name": "Funding Rate Contrarian Squeeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = deque(maxlen=6)
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.pos_funding_threshold = 0.00028  # 0.028% per 8h: crowded longs
        self.neg_funding_threshold = -0.00012  # -0.012% per 8h: crowded shorts
        self.persist_window = 3

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Track funding rate history
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)

        if len(self.funding_history) < self.persist_window:
            return None

        recent_funding = list(self.funding_history)[-self.persist_window :]
        avg_funding = sum(recent_funding) / len(recent_funding)

        rsi = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        price = ctx.bar.close
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Active position management and exit rules
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Long exit: funding normalized / positive, or RSI overbought
            if pos_dir == "long":
                if avg_funding >= 0.00008 or rsi >= 68.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_or_funding_normalized",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": price,
                            "bars_held": bars_held,
                        },
                    )

            # Short exit: funding normalized / negative, or RSI oversold
            elif pos_dir == "short":
                if avg_funding <= -0.00005 or rsi <= 32.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_or_funding_normalized",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": price,
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries during market panic meltdowns
        if crisis_score > 0.65:
            return None

        # Entry condition 1: Short squeeze setup (Negative funding persisted, crowd short)
        # Price filter: RSI not already overextended (>60) and not in a free-fall (RSI > 28)
        if avg_funding <= self.neg_funding_threshold:
            if 28.0 < rsi < 58.0 and price >= closes[-2] * 0.992:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + abs(avg_funding) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_negative_funding_squeeze",
                        "avg_funding": avg_funding,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                    },
                )

        # Entry condition 2: Long crowd fade setup (High positive funding persisted, crowd overleveraged long)
        # Price filter: RSI not already oversold (<40) and not parabolic breakout (RSI < 72)
        if avg_funding >= self.pos_funding_threshold:
            if 42.0 < rsi < 72.0 and price <= closes[-2] * 1.008:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + avg_funding * 800.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "contrarian_positive_funding_exhaustion",
                        "avg_funding": avg_funding,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                    },
                )

        return None