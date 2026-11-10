from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia close/reset
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_date: Optional[int] = None
        self.bars_since_last_exit: int = 999
        self.min_cooldown_bars: int = 4
        self.min_asia_drift_bps: float = 35.0   # Require clear directional momentum
        self.max_asia_drift_bps: float = 320.0  # Avoid entering after overextended blow-offs

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_last_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_exit += 1

        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        today_date = ts.year * 10000 + ts.month * 100 + ts.day
        current_price = ctx.bar.close

        # Track Asia session (UTC 00:00 to 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        if hour == 8:
            self.asia_close_price = current_price

        # Exit condition: Close any open position before the next Asia session begins (UTC 22:00-23:00)
        if ctx.has_position():
            if hour >= 22 or hour <= 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_session_exit",
                        "hour": hour,
                        "current_price": current_price
                    }
                )
            return None

        # Strictly trade once per calendar day at US session open window (UTC 13:00 or 14:00)
        if hour not in (13, 14):
            return None

        if self.last_trade_date == today_date:
            return None

        if self.bars_since_last_exit < self.min_cooldown_bars:
            return None

        if self.asia_open_price is None or self.asia_close_price is None:
            return None

        # Measure Asia session drift in basis points
        asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Regime & filter checks: Drift must be positive and bounded to prevent exhaustion buying
        if not (self.min_asia_drift_bps <= asia_drift_bps <= self.max_asia_drift_bps):
            return None

        # Trend filter: Price must trade above the 24-hour EMA for trend alignment
        ema24 = self._ema(closes, 24)
        if ema24 is None or current_price < ema24:
            return None

        # Market stress filter: Skip if market crisis is flagged
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        # Size confidence based on relative strength of drift
        drift_norm = (asia_drift_bps - self.min_asia_drift_bps) / (self.max_asia_drift_bps - self.min_asia_drift_bps)
        confidence = round(0.60 + 0.30 * min(max(drift_norm, 0.0), 1.0), 2)

        self.last_trade_date = today_date

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_drift_continuation_us_open",
                "asia_drift_bps": round(asia_drift_bps, 2),
                "asia_open": self.asia_open_price,
                "asia_close": self.asia_close_price,
                "ema24": round(ema24, 2),
                "crisis_score": round(crisis_score, 3)
            }
        )