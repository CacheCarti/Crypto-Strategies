from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended session hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.trend_ema_period = 30
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown of 4 bars following an exit
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.trend_ema_period + 5)
        highs = ctx.highs(self.lookback + self.trend_ema_period + 5)
        lows = ctx.lows(self.lookback + self.trend_ema_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        day_identifier = (current_time.year * 1000) + current_time.timetuple().tm_yday

        # Mandatory session closeout: Flatten positions by 21 UTC or later
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_flat",
                        "hour": current_hour,
                        "bar_index": ctx.bar_index,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Only evaluate new entries during US session open window (13:00 - 15:00 UTC)
        if not (13 <= current_hour <= 15):
            return None

        # Prevent multiple trades on the same day and enforce cooldown
        if self.last_traded_day == day_identifier or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Avoid entering during extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Measure 12-bar momentum (overnight/Asian/London lead-up)
        past_price = closes[-self.lookback]
        curr_price = closes[-1]
        mom_return_bps = ((curr_price - past_price) / past_price) * 10000.0

        ema_trend = self._ema(closes, self.trend_ema_period)
        atr = self._atr(highs, lows, closes, period=14)
        if ema_trend is None or atr is None:
            return None

        # Volatility normalization: ensure momentum is statistically distinct
        atr_bps = (atr / curr_price) * 10000.0
        threshold_bps = max(50.0, atr_bps * 0.4)

        # Bullish continuation: strong positive pre-session move confirmed by trend filter
        if mom_return_bps > threshold_bps and curr_price > ema_trend:
            self.last_traded_day = day_identifier
            conf = min(0.85, max(0.55, 0.55 + (mom_return_bps / (threshold_bps * 4.0))))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_return_bps": round(mom_return_bps, 2),
                    "threshold_bps": round(threshold_bps, 2),
                    "ema_trend": round(ema_trend, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        # Bearish continuation: strong negative pre-session move confirmed by trend filter
        if mom_return_bps < -threshold_bps and curr_price < ema_trend:
            self.last_traded_day = day_identifier
            conf = min(0.85, max(0.55, 0.55 + (abs(mom_return_bps) / (threshold_bps * 4.0))))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_return_bps": round(mom_return_bps, 2),
                    "threshold_bps": round(threshold_bps, 2),
                    "ema_trend": round(ema_trend, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        return None