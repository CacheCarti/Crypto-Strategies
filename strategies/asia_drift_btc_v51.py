from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.asia_open_price = None
        self.asia_close_price = None

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
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

        dt = ctx.bar.timestamp
        current_day = dt.toordinal()
        current_hour = dt.hour
        close_price = ctx.bar.close

        # Record Asia open at UTC 00:00
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia close at UTC 08:00
        if current_hour == 8:
            self.asia_close_price = close_price

        # Exit logic: close before the next Asia session starts (UTC 23:00) or by session rule
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": close_price}
                )
            return None

        # Mandatory cooldown between entries
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # US Session entry window: 13:00 to 15:00 UTC (US equity open alignment)
        if 13 <= current_hour <= 15:
            # One entry per calendar day
            if self.last_traded_day == current_day:
                return None

            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Meaningful positive drift during Asia session (> 25 bps)
                if asia_return_bps > 25.0:
                    ema20 = self._ema(closes, 20)
                    if ema20 is not None and close_price > ema20:
                        highs = ctx.highs(20)
                        lows = ctx.lows(20)
                        atr = self._atr(highs, lows, closes, 14) or (close_price * 0.005)

                        atr_bps = (atr / close_price) * 10000.0
                        sl_bps = max(150.0, min(300.0, atr_bps * 1.5))
                        tp_bps = max(250.0, min(500.0, sl_bps * 1.6))

                        confidence = min(0.85, max(0.55, 0.55 + (asia_return_bps / 500.0)))
                        self.last_traded_day = current_day
                        self.cooldown_until_bar = ctx.bar_index + 8

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=sl_bps,
                            take_profit_bps=tp_bps,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "positive_asia_drift_us_open_continuation",
                                "asia_return_bps": round(asia_return_bps, 2),
                                "ema20": round(ema20, 2),
                                "price": round(close_price, 2),
                                "hour": current_hour,
                                "sl_bps": round(sl_bps, 1),
                                "tp_bps": round(tp_bps, 1),
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 6)