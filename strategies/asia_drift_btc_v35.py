from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until close
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date: Optional[int] = None
        self.min_drift_bps: float = 35.0  # Clear positive drift requirement
        self.max_drift_bps: float = 350.0  # Avoid overextended blow-offs
        self.last_exit_bar: int = -50

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_date = bar_time.toordinal()

        # Capture Asia session open (UTC 00:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Scheduled exit before the next Asia session opens (UTC 22:00 - 23:00)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Entry window: US session open (UTC 13:00 - 14:00)
        # Ensure 1 trade per day and respect mandatory cooldown
        if current_hour in (13, 14):
            if self.last_traded_date == current_date:
                return None
            if (ctx.bar_index - self.last_exit_bar) < 6:
                return None
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            # Calculate Asia drift (price at US open vs Asia open)
            drift_bps = ((ctx.bar.close - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Trend & momentum confirmation to reject marginal/false continuation setups
            sma30 = self._sma(closes, 30)
            rsi = self._rsi(closes, 14)

            if sma30 is None or rsi is None:
                return None

            # High-conviction filter: meaningful drift, price above SMA, healthy RSI (not overbought)
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps:
                if ctx.bar.close > sma30 and 45.0 <= rsi <= 68.0:
                    self.last_traded_date = current_date
                    confidence = min(0.9, max(0.6, 0.6 + (drift_bps / 500.0)))
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_positive_drift_us_continuation",
                            "drift_bps": round(drift_bps, 2),
                            "rsi": round(rsi, 2),
                            "sma30": round(sma30, 2),
                            "hour": current_hour
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index