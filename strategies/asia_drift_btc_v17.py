from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to Asia close
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.current_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today = False
        self.last_exit_bar = -999

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.toordinal()
        current_hour = ts.hour

        # Reset daily state on a new calendar day
        if current_day != self.current_day:
            self.current_day = current_day
            self.traded_today = False
            self.asia_open_price = None
            self.asia_close_price = None

        # Capture Asia session open (UTC 0)
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session end (UTC 8)
        if current_hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = ctx.bar.close

        # Mandatory timed exit: close positions before Asia session re-opens (UTC 22 or 23)
        if ctx.has_position() and current_hour in (22, 23):
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "timed_session_close",
                    "hour": current_hour,
                    "close": ctx.bar.close
                }
            )

        # Skip if in position or already traded today or during recent exit cooldown
        if ctx.has_position() or self.traded_today:
            return None
        if ctx.bar_index - self.last_exit_bar < 4:
            return None

        # Entry window: US cash open / London overlap window (UTC 13-15)
        if current_hour in (13, 14, 15):
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Structural regime & trend filter: ensure price is above 24h moving average
            sma24 = sum(closes[-24:]) / 24.0
            price = ctx.bar.close

            # We need positive Asia drift with meaningful minimum magnitude (>= 25 bps) but not overstretched (<= 250 bps)
            if 25.0 <= asia_drift_bps <= 250.0 and price > sma24:
                # Dynamic volatility-based stop structure
                atr = self._atr(highs, lows, closes, 14)
                if atr is None or atr == 0:
                    sl_bps = self.METADATA["declared_sl_bps"]
                else:
                    atr_bps = (atr / price) * 10000.0
                    sl_bps = max(120.0, min(300.0, atr_bps * 1.5))

                tp_bps = sl_bps * 1.75
                self.traded_today = True

                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "asia_bullish_continuation",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1),
                        "price": price,
                        "sma24": round(sma24, 2)
                    }
                )

        return None