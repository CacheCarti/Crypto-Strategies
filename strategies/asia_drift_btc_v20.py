from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding until Asia begins)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day: Optional[int] = None
        self.traded_today: bool = False
        self.min_drift_bps: float = 30.0  # meaningful positive drift requirement
        self.bars_since_exit: int = 24

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1

        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        day_key = ts.toordinal()

        # Reset daily state on a new calendar day
        if self.current_day != day_key:
            self.current_day = day_key
            self.traded_today = False
            self.asia_open_price = None
            self.asia_close_price = None

        # Capture Asia session (UTC 00:00 to 08:00) open price at hour 0
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session close reference at hour 8
        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session begins (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Hard guard: do not enter if already holding a position or already traded today
        if self.traded_today or self.bars_since_exit < 3:
            return None

        # Strict event boundary: trigger entry evaluation exclusively at US market open (UTC 13:00 or 14:00)
        # UTC 13:00 corresponds to 8:00/9:00 AM US ET
        if current_hour not in (13, 14):
            return None

        # Require established Asia session benchmarks
        if self.asia_open_price is None or self.asia_open_price <= 0:
            return None

        asia_close = self.asia_close_price if self.asia_close_price is not None else closes[-6]
        drift_bps = ((asia_close - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Filter: Asia drift must be solidly positive, avoiding flat/noisy drift
        if drift_bps < self.min_drift_bps:
            return None

        # Trend and momentum alignment filter: price must be above 30-period EMA
        ema30 = self._ema(closes, 30)
        if ema30 is None or ctx.bar.close <= ema30:
            return None

        # Regime safety: avoid entering during extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Confidence scaled by drift strength
        confidence = min(0.9, max(0.55, 0.55 + (drift_bps / 500.0) * 0.3))

        self.traded_today = True

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_drift_continuation_us_open",
                "drift_bps": round(drift_bps, 2),
                "asia_open": round(self.asia_open_price, 2),
                "asia_close": round(asia_close, 2),
                "ema30": round(ema30, 2),
                "crisis_score": round(crisis_score, 2),
                "hour": current_hour
            }
        )