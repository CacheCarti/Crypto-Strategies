from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to 20:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day_str: Optional[str] = None
        self.min_drift_bps = 25.0  # Drift filter: must show genuine positive momentum (>0.25%)
        self.max_drift_bps = 350.0  # Cap overextended drifts to avoid buying exhaustion tops

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        day_str = ts.strftime("%Y-%m-%d")

        # Track daily rollover
        if self.current_day_str != day_str:
            self.current_day_str = day_str
            self.asia_open_price = None
            self.asia_close_price = None

        # Asia Session is UTC 00:00 - 08:00
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        if hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Position Exit Rule: Close before next Asia session (UTC 21:00-23:00)
        if ctx.has_position():
            if hour >= 21 or hour < 8:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "pre_asia_session_exit", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Entry Rule: Evaluate at US session open (UTC 13:00 - 14:00)
        if 13 <= hour <= 14:
            if self.last_traded_date == day_str:
                return None  # Strictly max one trade per day

            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate drift across Asia hours
            drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Guard against negative or overextended drift
            if not (self.min_drift_bps <= drift_bps <= self.max_drift_bps):
                return None

            # Market context & trend filter: ensure price is above intermediate EMA 24
            ema24 = self._ema(closes, 24)
            if ema24 is None or ctx.bar.close < ema24:
                return None

            # Volatility regime filter: skip MELTDOWN or extreme crisis
            crisis_score = ctx.market.get("crisis_score", 0.0)
            if crisis_score > 0.6:
                return None

            # ATR-based dynamic risk parameters
            highs = ctx.highs(20)
            lows = ctx.lows(20)
            atr = self._atr(highs, lows, closes, 14)
            if atr is None or ctx.bar.close <= 0:
                return None

            atr_bps = (atr / ctx.bar.close) * 10000.0
            sl_bps = max(150.0, min(320.0, atr_bps * 1.5))
            tp_bps = max(250.0, min(500.0, sl_bps * 1.6))

            # Scaled confidence based on drift strength
            confidence = min(0.9, max(0.55, 0.55 + (drift_bps / 500.0)))
            self.last_traded_date = day_str

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_drift_continuation_entry",
                    "drift_bps": round(drift_bps, 2),
                    "ema24": round(ema24, 2),
                    "atr_bps": round(atr_bps, 2),
                    "hour": hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass