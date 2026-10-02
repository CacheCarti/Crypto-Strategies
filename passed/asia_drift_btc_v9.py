from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.current_day: Optional[int] = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today: bool = False
        self.last_exit_bar: int = -50
        self.min_cooldown_bars: int = 4
        self.min_asia_drift_bps: float = 35.0
        self.ema_period: int = 30
        self.rsi_period: int = 14

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_day = bar_time.toordinal()
        hour = bar_time.hour

        # Reset daily session tracker on new calendar day
        if self.current_day != current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open
        elif hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Handle planned end-of-day exit before the next Asia session
        if ctx.has_position():
            if hour >= 21:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": hour,
                        "close_price": ctx.bar.close,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Check entry gating
        if self.traded_today or ctx.has_position():
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.min_cooldown_bars:
            return None

        # US Session Open window (UTC 13:00 - 14:00)
        if hour in (13, 14):
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
            if drift_bps < self.min_asia_drift_bps:
                return None

            ema_val = self._ema(closes, self.ema_period)
            rsi_val = self._rsi(closes, self.rsi_period)
            if ema_val is None or rsi_val is None:
                return None

            fear_greed = ctx.features.get("fear_greed_index", 50.0)

            # Trend & momentum confirmation filters
            trend_bullish = ctx.bar.close > ema_val
            rsi_supportive = 48.0 <= rsi_val <= 72.0
            sentiment_ok = fear_greed >= 30.0

            if trend_bullish and rsi_supportive and sentiment_ok:
                # Dynamic confidence scaling
                drift_factor = min(max((drift_bps - self.min_asia_drift_bps) / 100.0, 0.0), 0.25)
                confidence = round(0.65 + drift_factor, 2)

                self.traded_today = True

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_drift_us_continuation",
                        "asia_drift_bps": round(drift_bps, 2),
                        "asia_open": round(self.asia_open_price, 2),
                        "asia_close": round(self.asia_close_price, 2),
                        "rsi": round(rsi_val, 2),
                        "ema30": round(ema_val, 2),
                        "fear_greed": fear_greed,
                        "entry_hour": hour,
                    },
                )

        return None