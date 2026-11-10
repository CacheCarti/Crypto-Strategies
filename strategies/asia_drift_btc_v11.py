from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 4
        self.entry_bar_idx: Optional[int] = None

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Manage open position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - (self.entry_bar_idx or ctx.bar_index)
            # Mandatory exit before next Asia session (UTC 23:00) or max hold time of 6 bars (6 hours)
            if current_hour == 23 or bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "bars_held": bars_held}
                )
            return None

        # Guard: cooldown after last trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Trade only once per calendar day at US session open window (UTC 13:00 - 15:00)
        if self.last_trade_day == current_day:
            return None

        if current_hour not in (13, 14, 15):
            return None

        if self.asia_open_price is None or self.asia_close_price is None:
            return None

        # Asia drift calculation
        asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Require meaningful Asia upward drift (> 25 bps) but avoid extreme overextensions (< 250 bps)
        if not (25.0 < asia_drift_bps < 250.0):
            return None

        # Trend and volatility confirmation at US open
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        if ema_fast is None or ema_slow is None:
            return None

        # US continuation entry: Price must be above medium EMA and fast EMA above slow EMA
        if ctx.bar.close > ema_fast and ema_fast > ema_slow:
            highs = ctx.highs(20)
            lows = ctx.lows(20)
            atr = self._atr(highs, lows, closes, 14)
            atr_pct = (atr / ctx.bar.close) * 10000.0 if atr else 50.0

            # Scale stops dynamically with recent volatility
            sl_bps = max(150.0, min(300.0, atr_pct * 1.5))
            tp_bps = max(250.0, min(450.0, sl_bps * 1.5))

            self.last_trade_day = current_day
            self.entry_bar_idx = ctx.bar_index

            confidence = 0.65
            if asia_drift_bps > 60.0:
                confidence = 0.80

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "asia_positive_drift_us_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": ctx.bar.close,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = None