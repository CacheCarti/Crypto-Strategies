from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_threshold = 0.00015
        self.persist_bars = 3
        self.cooldown_period = 6
        self.last_exit_bar = -100
        self.entry_bar = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        if not hasattr(self, "funding_history"):
            self.funding_history = []
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Dynamic volatility stop calculation via ATR
        atr = self._atr(highs, lows, closes, 14)
        atr_bps = (atr / closes[-1]) * 10000.0 if (atr and closes[-1] > 0) else 150.0
        dyn_sl = max(180.0, min(350.0, atr_bps * 1.8))
        dyn_tp = dyn_sl * 1.6

        # Check exit conditions for open positions
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            # Exit 1: Funding normalizes toward zero
            funding_normalized = abs(current_funding) < 0.00003
            # Exit 2: Time-based decay if squeeze fails to trigger within 12 bars (12h)
            time_decay = bars_held >= 12

            if pos_dir == "long" and (funding_normalized or time_decay or current_funding > self.funding_threshold):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_normalize_or_timeout",
                    "funding": current_funding,
                    "bars_held": bars_held
                })

            if pos_dir == "short" and (funding_normalized or time_decay or current_funding < -self.funding_threshold):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_normalize_or_timeout",
                    "funding": current_funding,
                    "bars_held": bars_held
                })

            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        if len(self.funding_history) < self.persist_bars:
            return None

        recent_funding = self.funding_history[-self.persist_bars:]
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        # Trend filter: Avoid shorting into severe runaway bull or buying into severe collapse
        price = closes[-1]
        dist_ema_bps = ((price - ema20) / ema20) * 10000.0

        # Long Squeeze Setup: Funding persistently negative, price not completely detached downward
        long_persist = all(f <= -self.funding_threshold for f in recent_funding)
        if long_persist and dist_ema_bps > -250.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.55 + abs(current_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "negative_funding_squeeze_persist",
                    "funding": current_funding,
                    "atr_bps": atr_bps,
                    "dist_ema_bps": dist_ema_bps
                }
            )

        # Short Squeeze/Fade Setup: Funding persistently positive, price not completely detached upward
        short_persist = all(f >= self.funding_threshold for f in recent_funding)
        if short_persist and dist_ema_bps < 250.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.55 + abs(current_funding) * 1000.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "positive_funding_overheat_persist",
                    "funding": current_funding,
                    "atr_bps": atr_bps,
                    "dist_ema_bps": dist_ema_bps
                }
            )

        return None