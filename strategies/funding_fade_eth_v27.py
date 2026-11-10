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
        self.funding_window = 3
        self.extreme_long_thresh = -0.00015
        self.extreme_short_thresh = 0.00035
        self.normalize_thresh = 0.00005
        self.cooldown_bars = 8
        self.bars_since_exit = 10
        self.last_close_bar = -999

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
        self.last_close_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.funding_window:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.funding_window:
            return None

        avg_funding = sum(self.funding_history) / len(self.funding_history)
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close
        bars_since_last_exit = ctx.bar_index - self.last_close_bar

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and avg_funding > -self.normalize_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "avg_funding": avg_funding, "rsi": rsi14}
                )
            if pos_dir == "short" and avg_funding < self.normalize_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "avg_funding": avg_funding, "rsi": rsi14}
                )
            return None

        if bars_since_last_exit < self.cooldown_bars:
            return None

        # Long: Crowd is persistently paying to short (squeeze setup), price not in freefall collapse
        crowd_extreme_short = all(f < self.extreme_long_thresh for f in self.funding_history)
        if crowd_extreme_short and current_price >= ema20 * 0.985 and rsi14 > 32.0:
            conf = min(0.9, 0.6 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_short_crowd_squeeze",
                    "avg_funding": avg_funding,
                    "rsi": rsi14,
                    "ema20": ema20,
                    "price": current_price
                }
            )

        # Short: Crowd is persistently paying to long (euphoria fade), price not parabolic runaway
        crowd_extreme_long = all(f > self.extreme_short_thresh for f in self.funding_history)
        if crowd_extreme_long and current_price <= ema20 * 1.02 and rsi14 < 68.0:
            conf = min(0.9, 0.6 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_long_crowd_fade",
                    "avg_funding": avg_funding,
                    "rsi": rsi14,
                    "ema20": ema20,
                    "price": current_price
                }
            )

        return None