from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours (13:00 to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.current_day: Optional[int] = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today: bool = False
        self.entry_bar_idx: int = -1
        self.min_cooldown_bars: int = 4

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        day_key = current_time.toordinal()
        hour = current_time.hour
        price = ctx.bar.close

        # Reset daily tracking
        if self.current_day != day_key:
            self.current_day = day_key
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Capture Asia session boundaries (00:00 to 08:00 UTC)
        if hour == 0:
            self.asia_open_price = ctx.bar.open
        elif hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = price

        # Cooldown guard based on exit / last trade
        in_cooldown = (ctx.bar_index - self.entry_bar_idx) < self.min_cooldown_bars

        # Timed exit: flatten before next Asia session begins (UTC 21-23)
        if ctx.has_position():
            if hour >= 21 or hour < 2:
                self.traded_today = True
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_pre_asia",
                    "hour": hour,
                    "price": price
                })
            return None

        # Entry logic: US Open window (13:00 to 15:00 UTC)
        if not self.traded_today and not in_cooldown and (13 <= hour <= 15):
            if self.asia_open_price is not None and self.asia_close_price is not None:
                drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Must exhibit clean positive momentum during Asia (at least +25 bps)
                if drift_bps >= 25.0:
                    # Filter: price must maintain local trend above 24h SMA
                    sma24 = self._sma(closes, 24)
                    atr14 = self._atr(highs, lows, closes, 14)

                    if sma24 is not None and atr14 is not None and price > sma24:
                        # Structure/Volatility-based SL scaling
                        atr_bps = (atr14 / price) * 10000.0
                        dynamic_sl = max(140.0, min(300.0, atr_bps * 1.5))
                        dynamic_tp = max(280.0, dynamic_sl * 1.8)

                        # Check broader market regime isn't in severe crisis
                        regime = ctx.market.get("regime", "NORMAL")
                        if regime in ("CRISIS", "MELTDOWN"):
                            return None

                        confidence = min(0.9, max(0.55, 0.55 + (drift_bps / 500.0)))
                        self.traded_today = True
                        self.entry_bar_idx = ctx.bar_index

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=dynamic_sl,
                            take_profit_bps=dynamic_tp,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_drift_continuation_us_open",
                                "asia_drift_bps": round(drift_bps, 2),
                                "atr_bps": round(atr_bps, 2),
                                "sma24": round(sma24, 2),
                                "price": price,
                                "hour": hour
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = ctx.bar_index