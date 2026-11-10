from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours (13:00 to 21:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_traded_date: Optional[str] = None
        self.last_exit_bar = -999
        self.cooldown_bars = 4

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        date_str = ts.strftime("%Y-%m-%d")

        # Record Asia session open (UTC 00:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia session close / end of window (UTC 08:00)
        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Check for mandatory session exit before next Asia session begins (UTC 21:00)
        if ctx.has_position():
            if hour >= 21 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "asia_session_end_of_day_exit",
                        "hour": hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Check trade gates: not in position, cooldown respected
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # US Session Open trigger window (UTC 13:00 to 14:00)
        if hour in (13, 14) and self.last_traded_date != date_str:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                # Calculate Asia drift return in bps
                drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Must be a solid, non-noisy positive drift (> 25 bps) but not overextended (< 250 bps)
                if 25.0 <= drift_bps <= 250.0:
                    # Filter: Overall medium-term trend confirmation (EMA24)
                    ema24 = self._ema(closes, 24)
                    if ema24 is not None and ctx.bar.close > ema24:
                        # Avoid crisis / extreme volatile regimes
                        regime = ctx.market.get("regime", "NORMAL")
                        if regime in ("CRISIS", "MELTDOWN"):
                            return None

                        confidence = min(0.9, 0.55 + (drift_bps / 500.0))
                        self.last_traded_date = date_str

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=220.0,
                            take_profit_bps=400.0,
                            horizon_seconds=28800,
                            metadata={
                                "reason": "asia_drift_us_continuation",
                                "drift_bps": round(drift_bps, 2),
                                "asia_open": self.asia_open_price,
                                "asia_close": self.asia_close_price,
                                "ema24": round(ema24, 2),
                                "price": ctx.bar.close,
                                "hour": hour,
                            }
                        )

        return None