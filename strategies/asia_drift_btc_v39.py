from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold (US session drift)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_date: Optional[int] = None
        self.current_day: Optional[int] = None
        self.session_traded_today: bool = False

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        bar = ctx.bar
        ts = bar.timestamp
        current_day = ts.toordinal() if hasattr(ts, "toordinal") else (ts.year * 1000 + ts.timetuple().tm_yday)
        hour = ts.hour

        # Reset daily state on calendar day transition
        if self.current_day != current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None
            self.session_traded_today = False

        # Capture Asia session open at UTC 00:00
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = bar.open

        # Capture Asia session close reference at UTC 08:00
        if hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = bar.close

        # Manage exits: Exit long before next Asia session begins (UTC 22 or 23)
        if ctx.has_position():
            if hour in (22, 23):
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": bar.close}
                )
            return None

        # Guard: Only one entry attempt per day, outside of crisis regime
        if self.session_traded_today or self.last_trade_date == current_day:
            return None

        # Trade window: US session open (UTC 13 to 15)
        if 13 <= hour <= 15:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            highs = ctx.highs(30)
            lows = ctx.lows(30)
            atr = self._atr(highs, lows, closes, period=14)
            if atr is None or atr <= 0:
                return None

            asia_drift = self.asia_close_price - self.asia_open_price
            drift_in_atr = asia_drift / atr

            # Dynamic threshold: Positive drift during Asia session exceeding 0.25 ATR
            ema20 = self._ema(closes, 20)
            trend_filter = ema20 is not None and bar.close > ema20

            if drift_in_atr > 0.25 and trend_filter:
                self.session_traded_today = True
                self.last_trade_date = current_day

                confidence = min(0.85, max(0.55, 0.55 + (drift_in_atr - 0.25) * 0.15))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=280.0,
                    take_profit_bps=520.0,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "drift_atr": round(drift_in_atr, 3),
                        "asia_drift": round(asia_drift, 2),
                        "atr": round(atr, 2),
                        "hour": hour,
                        "price": bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass