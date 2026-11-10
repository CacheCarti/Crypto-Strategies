from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.atr_period = 14
        self.min_mom_bps = 70.0
        self.last_traded_date = None
        self.last_exit_bar = -100
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)

        if len(closes) < self.lookback + self.atr_period + 2:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        has_pos = ctx.has_position()

        # Session forced exit at or after 21:00 UTC
        if has_pos:
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_pre_21_utc",
                    "hour": current_hour,
                    "price": ctx.bar.close
                })
            return None

        # Check entry window: US session open window (13:00 to 15:00 UTC)
        if current_hour not in (13, 14, 15):
            return None

        # Limit to 1 entry per calendar day and enforce post-exit cooldown
        if self.last_traded_date == current_date:
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate momentum over pre-US session (last 12 bars)
        p_now = closes[-1]
        p_prev = closes[-1 - self.lookback]
        mom_bps = ((p_now - p_prev) / p_prev) * 10000.0

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        atr_bps = (atr / p_now) * 10000.0
        # Require momentum to be significant relative to recent bar volatility
        if abs(mom_bps) < max(self.min_mom_bps, atr_bps * 0.75):
            return None

        # Trend filter: check if short-term impulse matches medium-term direction
        sma24 = sum(closes[-24:]) / 24.0

        if mom_bps > 0 and p_now > sma24:
            self.last_traded_date = current_date
            conf = min(1.0, 0.55 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bullish_continuation",
                    "mom_bps": round(mom_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "sma24": round(sma24, 2),
                    "price": p_now,
                    "hour": current_hour
                }
            )

        if mom_bps < -self.min_mom_bps and p_now < sma24:
            self.last_traded_date = current_date
            conf = min(1.0, 0.55 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bearish_continuation",
                    "mom_bps": round(mom_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "sma24": round(sma24, 2),
                    "price": p_now,
                    "hour": current_hour
                }
            )

        return None