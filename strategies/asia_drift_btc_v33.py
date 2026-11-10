from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold (13:00 to 22:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price = None
        self.asia_open_date = None
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour
        close = ctx.bar.close

        # Record Asia session open at 00:00 UTC
        if hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_open_date = current_date

        # Exit logic: Close position before next Asia session begins (UTC 22:00 or 23:00)
        if ctx.has_position():
            if hour >= 22 or hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "close": close}
                )
            return None

        # Entry window: US morning open window (13:00 or 14:00 UTC)
        # Guarantees at most 1 trade per day
        if hour in (13, 14):
            if self.last_traded_date == current_date:
                return None
            if ctx.bar_index - self.last_exit_bar < 4:
                return None
            if self.asia_open_date != current_date or self.asia_open_price is None:
                return None

            # 1. Asia Drift: Measure drift from UTC 00:00 to current bar
            asia_drift_bps = ((close - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Drift must be positive and significant (>= 25 bps) but not overextended (<= 350 bps)
            if not (25.0 <= asia_drift_bps <= 350.0):
                return None

            # 2. Trend alignment: Price above 24h EMA (EMA 24 on 1h bars)
            ema24 = self._ema(closes, 24)
            if ema24 is None or close <= ema24:
                return None

            # 3. Dynamic sizing / confidence via ATR
            highs = ctx.highs(40)
            lows = ctx.lows(40)
            atr = self._atr(highs, lows, closes, 14)
            if atr is None:
                return None

            # Scale confidence with drift strength
            confidence = min(0.85, max(0.55, 0.5 + (asia_drift_bps / 500.0)))

            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "asia_positive_drift_us_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 1),
                    "ema24": round(ema24, 1),
                    "close": round(close, 1),
                    "atr": round(atr, 2),
                    "hour": hour
                }
            )

        return None