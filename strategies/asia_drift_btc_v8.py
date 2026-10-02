from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class AsiaDriftUsContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold time
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 24
        self.atr_period = 14
        self.min_drift_bps = 25.0
        self.max_drift_bps = 220.0
        
        # State tracking per calendar day
        self.current_date = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today = False
        self.bars_since_exit = 10

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
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(self.ema_period + 10)
        highs = ctx.highs(self.atr_period + 10)
        lows = ctx.lows(self.atr_period + 10)

        if len(closes) < self.ema_period:
            return None

        bar_time = ctx.bar.timestamp
        bar_date = bar_time.date()
        bar_hour = bar_time.hour

        # New day reset
        if self.current_date != bar_date:
            self.current_date = bar_date
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Capture Asia session (UTC 00:00 - 08:00) open and close anchors
        if bar_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open
        elif self.asia_open_price is None and bar_hour < 8:
            self.asia_open_price = ctx.bar.open

        if bar_hour == 8:
            self.asia_close_price = ctx.bar.close

        # End of day session exit (UTC 22:00 or later)
        if ctx.has_position():
            if bar_hour >= 22 or bar_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_before_asia",
                        "hour": bar_hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Mandatory cooldown between trades
        if self.bars_since_exit < 3:
            return None

        # Check entry trigger during early US Session (UTC 13:00 - 15:00)
        if 13 <= bar_hour <= 15 and not self.traded_today and not ctx.has_position():
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                ema_val = self._ema(closes, self.ema_period)
                atr_val = self._atr(highs, lows, closes, self.atr_period)

                if ema_val is None or atr_val is None:
                    return None

                # Trend alignment & positive moderate drift during Asia
                if self.min_drift_bps <= asia_drift_bps <= self.max_drift_bps:
                    if ctx.bar.close > ema_val:
                        # Scaled confidence based on drift strength
                        norm_drift = (asia_drift_bps - self.min_drift_bps) / (self.max_drift_bps - self.min_drift_bps)
                        confidence = min(0.85, max(0.55, 0.55 + 0.30 * norm_drift))

                        # Volatility-adjusted stop & profit
                        atr_bps = (atr_val / ctx.bar.close) * 10000.0
                        sl_bps = max(180.0, min(350.0, atr_bps * 1.5))
                        tp_bps = max(320.0, min(600.0, atr_bps * 2.5))

                        self.traded_today = True

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=sl_bps,
                            take_profit_bps=tp_bps,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_drift_us_continuation",
                                "asia_drift_bps": round(asia_drift_bps, 2),
                                "ema24": round(ema_val, 2),
                                "atr14_bps": round(atr_bps, 2),
                                "hour": bar_hour,
                                "price": ctx.bar.close,
                            },
                        )

        return None