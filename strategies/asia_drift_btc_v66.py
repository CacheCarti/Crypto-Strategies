from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until end of US session
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.cooldown_until_bar = 0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        current_day = ts.toordinal()

        # Track Asia open (00:00 UTC)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia close (08:00 UTC)
        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close any open position before the next Asia session begins (22:00-23:00 UTC)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour}
                )
            return None

        # Check entry gating: cooldown and strictly once per calendar day
        if ctx.bar_index < self.cooldown_until_bar or self.last_traded_day == current_day:
            return None

        # US Session Open window: 13:00 or 14:00 UTC
        if current_hour in (13, 14):
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia drift in basis points
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Trend & momentum confirmation filters
            ema30 = self._ema(closes, 30)
            if ema30 is None:
                return None

            # Filter: Meaningful positive Asia drift (> +35 bps), current price holding above Asia close & EMA
            if asia_drift_bps > 35.0 and ctx.bar.close > self.asia_close_price and ctx.bar.close > ema30:
                self.last_traded_day = current_day
                conf = min(0.85, 0.55 + (asia_drift_bps / 500.0) * 0.25)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_positive_drift_us_open_long",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "hour": current_hour,
                        "ema30": round(ema30, 2),
                        "close": round(ctx.bar.close, 2)
                    }
                )

        return None