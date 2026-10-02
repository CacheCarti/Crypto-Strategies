from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.ema_period = 20
        self.min_momentum_pct = 0.005  # 0.5% move in 12 bars
        self.last_trade_day = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 10)
        if len(closes) < self.ema_period + 10:
            return None

        dt = ctx.bar.timestamp
        current_day = (dt.year, dt.month, dt.day)

        # Mandatory session exit at or after UTC 21:00
        if ctx.has_position() and dt.hour >= 21:
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "session_close_utc21",
                    "hour": dt.hour,
                    "price": ctx.bar.close,
                }
            )

        if ctx.has_position():
            return None

        # Reset daily trade tracker on new day
        if self.last_trade_day != current_day:
            self.last_trade_day = None

        # Only evaluate entry during early US session hours (UTC 13-14) and one trade per day
        if dt.hour not in (13, 14) or self.last_trade_day == current_day:
            return None

        # Market regime filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 12-bar momentum calculation
        p_now = closes[-1]
        p_past = closes[-self.lookback - 1]
        momentum = (p_now - p_past) / p_past

        ema20 = self._ema(closes, self.ema_period)
        if ema20 is None:
            return None

        # Long continuation setup
        if momentum >= self.min_momentum_pct and p_now > ema20:
            self.last_trade_day = current_day
            confidence = min(1.0, 0.5 + abs(momentum) * 20.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "momentum_12": round(momentum, 5),
                    "ema20": round(ema20, 2),
                    "price": p_now,
                    "hour": dt.hour,
                }
            )

        # Short continuation setup
        if momentum <= -self.min_momentum_pct and p_now < ema20:
            self.last_trade_day = current_day
            confidence = min(1.0, 0.5 + abs(momentum) * 20.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "momentum_12": round(momentum, 5),
                    "ema20": round(ema20, 2),
                    "price": p_now,
                    "hour": dt.hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass