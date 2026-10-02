from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 25200,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 12
        self.ema_period = 28
        self.rsi_period = 14
        self.min_mom_bps = 55.0
        self.last_trade_day = None

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        needed_bars = max(self.ema_period, self.momentum_period) + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        dt = ctx.bar.timestamp
        hour = dt.hour
        today_key = (dt.year, dt.month, dt.day)

        # 1. Position Management: Close position at or after UTC 21:00 or before US session (UTC 13:00)
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc21_flatten",
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # 2. Daily Entry Gate & Time Window Filter
        # Only allow entries during the US opening momentum window (UTC 13, 14, 15)
        if today_key == self.last_trade_day or not (13 <= hour <= 15):
            return None

        # Regime safety check
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 3. Calculate Indicators
        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        if ema_val is None or rsi_val is None:
            return None

        prior_close = closes[-1 - self.momentum_period]
        if prior_close <= 0:
            return None
        mom_ret = (closes[-1] - prior_close) / prior_close
        mom_bps = mom_ret * 10000.0

        current_price = ctx.bar.close

        # 4. Long Continuation Setup
        if mom_bps >= self.min_mom_bps and current_price > ema_val and 50.0 <= rsi_val <= 72.0:
            self.last_trade_day = today_key
            conf = min(0.90, max(0.55, 0.50 + (mom_bps / 300.0)))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "mom_12h_bps": round(mom_bps, 1),
                    "rsi": round(rsi_val, 2),
                    "ema": round(ema_val, 2),
                    "price": current_price,
                    "hour": hour,
                },
            )

        # 5. Short Continuation Setup
        if mom_bps <= -self.min_mom_bps and current_price < ema_val and 28.0 <= rsi_val <= 50.0:
            self.last_trade_day = today_key
            conf = min(0.90, max(0.55, 0.50 + (abs(mom_bps) / 300.0)))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "mom_12h_bps": round(mom_bps, 1),
                    "rsi": round(rsi_val, 2),
                    "ema": round(ema_val, 2),
                    "price": current_price,
                    "hour": hour,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass