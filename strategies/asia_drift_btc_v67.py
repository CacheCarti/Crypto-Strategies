from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to 20:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.last_traded_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day = -1

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
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
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        dt = ctx.bar.timestamp
        current_day = dt.date().toordinal()
        current_hour = dt.hour

        # Reset daily drift state on day transition
        if current_day != self.current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None

        # Capture Asia session opening price (at UTC 00:00)
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session close / post-Asia level (around UTC 08:00)
        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Volatility & trend context
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr_val = self._atr(highs, lows, closes, 14)
        ema30 = self._ema(closes, 30)

        # Timed exit: flatten before Asia session reopens to avoid overnight reversal chop
        if ctx.has_position():
            if current_hour >= 21 or current_hour == 22:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # One trade per calendar day gate
        if self.last_traded_day == current_day:
            return None

        # Entry window: US session open/early liquidity window (UTC 13:00 - 15:00)
        if 13 <= current_hour <= 15:
            # Need valid Asia drift measurements
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate drift during Asia (0 to 8 UTC)
            drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Filter 1: Positive, meaningful Asia drift (> 15 bps drift, avoiding flat noise)
            # Filter 2: Macro alignment - Price above EMA30 indicates higher timeframe support
            if drift_bps > 15.0 and ema30 is not None and ctx.bar.close > ema30:
                # Volatility-based structural stop calculation
                price = ctx.bar.close
                if atr_val is not None and price > 0:
                    atr_bps = (atr_val / price) * 10000.0
                    dynamic_sl = max(120.0, min(300.0, atr_bps * 1.5))
                    dynamic_tp = max(240.0, min(600.0, dynamic_sl * 2.0))
                else:
                    dynamic_sl = 200.0
                    dynamic_tp = 400.0

                self.last_traded_day = current_day
                conf = min(0.9, max(0.55, 0.5 + (drift_bps / 200.0)))

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=25200,
                    metadata={
                        "reason": "asia_drift_bullish_continuation",
                        "drift_bps": round(drift_bps, 2),
                        "asia_open": self.asia_open_price,
                        "asia_close": self.asia_close_price,
                        "ema30": round(ema30, 2),
                        "atr_bps": round(dynamic_sl, 2),
                        "hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass