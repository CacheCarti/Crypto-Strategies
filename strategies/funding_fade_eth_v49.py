from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
from collections import deque

class FundingRateEventContrarian(Strategy):
    METADATA = {
        "name": "FundingRateEventContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = deque(maxlen=8)
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.extreme_long_thresh = -0.00020
        self.extreme_short_thresh = 0.00035
        self.neutral_upper = 0.00010
        self.neutral_lower = -0.00005

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        if has_pos:
            if pos_dir == "long" and current_funding >= self.neutral_lower:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_exit_long", "funding": current_funding, "close": ctx.bar.close}
                )
            if pos_dir == "short" and current_funding <= self.neutral_upper:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_exit_short", "funding": current_funding, "close": ctx.bar.close}
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if len(self.funding_history) < 3:
            return None

        recent_funding = list(self.funding_history)[-3:]
        is_persistently_short_crowded = all(f <= self.extreme_long_thresh for f in recent_funding)
        is_persistently_long_crowded = all(f >= self.extreme_short_thresh for f in recent_funding)

        if not (is_persistently_short_crowded or is_persistently_long_crowded):
            return None

        sma50 = self._sma(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if sma50 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        if is_persistently_short_crowded:
            not_in_hard_crash = current_price >= (sma50 * 0.94) and rsi14 >= 30.0
            if not_in_hard_crash:
                conf = min(0.9, 0.6 + abs(current_funding) * 500.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=400.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "short_squeeze_persistent_negative_funding",
                        "funding": current_funding,
                        "rsi14": rsi14,
                        "sma50": sma50,
                        "close": current_price
                    }
                )

        if is_persistently_long_crowded:
            not_in_parabolic_meltup = current_price <= (sma50 * 1.06) and rsi14 <= 70.0
            if not_in_parabolic_meltup:
                conf = min(0.9, 0.6 + abs(current_funding) * 400.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=400.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "long_liquidation_persistent_positive_funding",
                        "funding": current_funding,
                        "rsi14": rsi14,
                        "sma50": sma50,
                        "close": current_price
                    }
                )

        return None