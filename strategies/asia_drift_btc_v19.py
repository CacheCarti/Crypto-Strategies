from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 UTC through 20:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        current_date = ts.date()
        hour = ts.hour

        # Reset daily drift tracking on a new calendar day
        if self.current_day != current_date:
            self.current_day = current_date
            self.asia_open_price = None
            self.asia_close_price = None

        # Capture Asia session open at 00:00 UTC
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session close at 08:00 UTC
        if hour == 8 and self.asia_open_price is not None and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Manage exit before next Asia session starts (at 21:00 or 22:00 UTC)
        if ctx.has_position():
            if hour >= 21 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Gated entry: precisely at US session open (13:00 or 14:00 UTC), strictly once per day
        if hour in (13, 14) and not ctx.has_position():
            if self.last_traded_date == current_date:
                return None

            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate drift scaled by local volatility (ATR-normalized to avoid fixed thresholds)
            drift = self.asia_close_price - self.asia_open_price
            atr = self._atr(highs, lows, closes, period=14)
            if atr is None or atr <= 0:
                return None

            # Trend filter: fast EMA over medium EMA for structural confirmation
            ema_fast = self._ema(closes, 12)
            ema_slow = self._ema(closes, 26)

            # Avoid entering during extreme market stress/meltdown
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            # Bullish Asia drift: drift > 0.3 * ATR and positive structural trend
            drift_ratio = drift / atr
            if drift_ratio > 0.3 and (ema_fast is not None and ema_slow is not None and ema_fast >= ema_slow):
                confidence = min(0.85, max(0.55, 0.55 + 0.15 * (drift_ratio - 0.3)))
                self.last_traded_date = current_date
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "drift_ratio": round(drift_ratio, 3),
                        "drift": round(drift, 2),
                        "atr": round(atr, 2),
                        "price": ctx.bar.close,
                        "ema_diff": round(ema_fast - ema_slow, 2)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass