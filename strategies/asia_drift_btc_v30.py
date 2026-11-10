from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours (13:00 to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -50

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_key = current_time.year * 1000 + current_time.timetuple().tm_yday

        # Record Asia session open (UTC 00:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia session close / London transition (UTC 08:00)
        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session (UTC 22:00)
        if ctx.has_position():
            if hour >= 22 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown: minimum 8 bars after last exit
        if (ctx.bar_index - self.last_exit_bar) < 8:
            return None

        # Only evaluate entry at the precise event boundary: US session open (13:00 UTC)
        # and enforce strict maximum 1 trade per calendar day
        if hour != 13 or self.last_trade_day == day_key:
            return None

        if self.asia_open_price is None or self.asia_close_price is None:
            return None

        # Calculate Asia drift in basis points
        asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Asia must show clear upward momentum: > 35 bps and not over-extended (< 350 bps)
        if not (35.0 <= asia_drift_bps <= 350.0):
            return None

        # Trend and volatility confirmation at US open
        ema24 = self._ema(closes, 24)
        atr14 = self._atr(highs, lows, closes, 14)
        if ema24 is None or atr14 is None:
            return None

        # Entry filter: US open price must confirm continuation by holding above Asia close & short EMA
        current_close = ctx.bar.close
        if current_close < self.asia_close_price or current_close < ema24:
            return None

        # Avoid extreme crisis / meltdown states
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Dynamic stop loss based on recent volatility, bounded reasonably
        atr_bps = (atr14 / current_close) * 10000.0
        sl_bps = max(120.0, min(280.0, atr_bps * 1.5))
        tp_bps = max(240.0, min(450.0, sl_bps * 1.7))

        self.last_trade_day = day_key

        confidence = 0.65
        if asia_drift_bps > 100.0:
            confidence = 0.80

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_updrift_us_continuation",
                "asia_drift_bps": round(asia_drift_bps, 2),
                "asia_open": self.asia_open_price,
                "asia_close": self.asia_close_price,
                "ema24": round(ema24, 2),
                "atr_bps": round(atr_bps, 2)
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index