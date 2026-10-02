from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class USSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours max hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 12
        self.trend_ema_period = 24
        self.mom_threshold_pct = 0.55
        self.last_trade_date = None
        self.last_exit_bar = -999
        self.cooldown_bars = 3

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 5)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        hour = ts.hour
        current_date = (ts.year, ts.month, ts.day)
        is_weekday = ts.weekday() < 5  # Mon-Fri for US equity alignment

        # Position management: Force flat at US session close (UTC 21)
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_flat",
                        "hour": hour,
                        "close_price": current_price,
                    },
                )
            return None

        # Entry gating: strictly US open window (UTC 13-15), Mon-Fri, 1 trade per day
        if not is_weekday or not (13 <= hour <= 15):
            return None

        if self.last_trade_date == current_date:
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate momentum and trend indicator
        past_price = closes[-self.momentum_period]
        if past_price <= 0:
            return None

        mom_pct = ((current_price - past_price) / past_price) * 100.0
        ema_trend = self._ema(closes, self.trend_ema_period)
        if ema_trend is None:
            return None

        # Long Setup: Strong positive pre-market momentum confirmed by trend
        if mom_pct >= self.mom_threshold_pct and current_price > ema_trend:
            self.last_trade_date = current_date
            confidence = min(0.9, 0.55 + (mom_pct / 5.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_momentum_continuation",
                    "momentum_pct": round(mom_pct, 3),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(current_price, 2),
                    "hour": hour,
                },
            )

        # Short Setup: Strong negative pre-market momentum confirmed by trend
        if mom_pct <= -self.mom_threshold_pct and current_price < ema_trend:
            self.last_trade_date = current_date
            confidence = min(0.9, 0.55 + (abs(mom_pct) / 5.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_momentum_continuation",
                    "momentum_pct": round(mom_pct, 3),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(current_price, 2),
                    "hour": hour,
                },
            )

        return None