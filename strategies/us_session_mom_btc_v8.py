from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionMomentumContinuation(Strategy):
    METADATA = {
        "name": "USSessionMomentumContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback: int = 10
        self.ema_period: int = 20
        self.min_momentum_bps: float = 55.0
        self.entry_start_hour: int = 13
        self.entry_end_hour: int = 15
        self.session_close_hour: int = 21
        self.last_traded_day: Optional[Any] = None

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + self.lookback + 5)
        if len(closes) < self.ema_period + self.lookback:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.date()
        current_price = ctx.bar.close

        # Mandatory session exit before UTC 21:00
        if ctx.has_position():
            if current_hour >= self.session_close_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close",
                        "hour": current_hour,
                        "price": current_price,
                        "position_direction": ctx.position_direction(),
                    }
                )
            return None

        # Entry window: US Market Open (UTC 13-15) & Max 1 trade per day
        if not (self.entry_start_hour <= current_hour <= self.entry_end_hour):
            return None

        if self.last_traded_day == current_day:
            return None

        # Measure pre-session momentum (last 10 bars)
        ref_price = closes[-self.lookback - 1]
        if ref_price <= 0:
            return None
        momentum_bps = ((current_price - ref_price) / ref_price) * 10000.0

        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Long continuation: clear upward momentum confirmed above EMA20
        if momentum_bps >= self.min_momentum_bps and current_price > ema:
            self.last_traded_day = current_day
            confidence = min(0.9, max(0.6, 0.6 + (momentum_bps / 500.0)))
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "momentum_bps": round(momentum_bps, 2),
                    "ema20": round(ema, 2),
                    "price": current_price,
                    "hour": current_hour,
                }
            )

        # Short continuation: clear downward momentum confirmed below EMA20
        if momentum_bps <= -self.min_momentum_bps and current_price < ema:
            self.last_traded_day = current_day
            confidence = min(0.9, max(0.6, 0.6 + (abs(momentum_bps) / 500.0)))
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "momentum_bps": round(momentum_bps, 2),
                    "ema20": round(ema, 2),
                    "price": current_price,
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass