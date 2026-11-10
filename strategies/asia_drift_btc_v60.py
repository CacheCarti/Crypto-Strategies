from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 25200,  # ~7 hours (hold through US session until Asia reset)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.bars_since_exit: int = 24
        self.min_cooldown_bars: int = 12
        self.min_drift_bps: float = 35.0  # require meaningful Asia expansion
        self.max_drift_bps: float = 400.0  # avoid overextended blow-offs

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        self.bars_since_exit += 1
        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Track Asia open anchor at 00:00 UTC
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Flat close condition: exit before Asia session resumes (at or after 22:00 UTC)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "asia_session_preclose",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Restrict entry check strictly to the US session open window (13:00 - 15:00 UTC)
        if current_hour not in (13, 14):
            return None

        # Check single trade per calendar day restriction & cooldown
        if self.last_trade_day == current_day:
            return None
        if self.bars_since_exit < self.min_cooldown_bars:
            return None

        if self.asia_open_price is None or self.asia_open_price <= 0:
            return None

        # Calculate session drift from Asia open
        current_price = ctx.bar.close
        drift_bps = ((current_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Asia drift must be distinctly positive and non-exhausted
        if drift_bps < self.min_drift_bps or drift_bps > self.max_drift_bps:
            return None

        # Trend and structural alignment: price must be trading above 20-period EMA
        ema20 = self._ema(closes, 20)
        if ema20 is None or current_price <= ema20:
            return None

        # Avoid entries during broad crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Signal execution
        self.last_trade_day = current_day
        self.bars_since_exit = 0

        # Confidence scaled by drift strength
        confidence = min(0.85, max(0.55, 0.55 + (drift_bps - self.min_drift_bps) / 600.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_drift_us_continuation",
                "asia_open": round(self.asia_open_price, 2),
                "drift_bps": round(drift_bps, 2),
                "ema20": round(ema20, 2),
                "crisis_score": round(crisis_score, 3),
                "entry_hour": current_hour
            }
        )