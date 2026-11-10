from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingSqueezeFader(Strategy):
    METADATA = {
        "name": "EthFundingSqueezeFader",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_thresh = -0.00015
        self.funding_short_thresh = 0.00035
        self.persistence_needed = 3
        self.cooldown_period = 8
        self.last_exit_bar = -100
        self.funding_history = []

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        price = ctx.bar.close
        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)

        if ema50 is None or rsi14 is None:
            return None

        pos_dir = ctx.position_direction()

        # Exit handling: normalize funding or mean reversion target hit
        if pos_dir == "long":
            if current_funding >= 0.0 or rsi14 >= 62.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_or_overbought", "funding": current_funding, "rsi": rsi14}
                )
            return None

        if pos_dir == "short":
            if current_funding <= 0.00005 or rsi14 <= 38.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_or_oversold", "funding": current_funding, "rsi": rsi14}
                )
            return None

        # Entry logic
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        if len(self.funding_history) < self.persistence_needed:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:]
        persistent_negative = all(f <= self.funding_long_thresh for f in recent_funding)
        persistent_positive = all(f >= self.funding_short_thresh for f in recent_funding)

        # Event boundary: Price turning after extreme crowd positioning
        # Long: Crowd is persistently paying to short, price stabilizes/rebounds above low of recent dip without falling off cliff
        if persistent_negative:
            # Avoid fading run-away crashes: RSI must be turning up from deeply oversold but not dead momentum
            if 30.0 <= rsi14 <= 48.0 and price >= closes[-2]:
                confidence = min(0.85, 0.55 + abs(current_funding) * 500.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_negative_squeeze_reversal",
                        "funding": current_funding,
                        "rsi": rsi14,
                        "ema50": ema50,
                        "price": price
                    }
                )

        # Short: Crowd is persistently overleveraged long, price stalls below recent high
        if persistent_positive:
            # Avoid fading runaway parabolic bull market without exhaustion
            if 52.0 <= rsi14 <= 70.0 and price <= closes[-2]:
                confidence = min(0.85, 0.55 + current_funding * 400.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_positive_exhaustion_fade",
                        "funding": current_funding,
                        "rsi": rsi14,
                        "ema50": ema50,
                        "price": price
                    }
                )

        return None