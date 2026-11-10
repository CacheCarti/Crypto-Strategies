from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia reopen
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.current_day: Optional[int] = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today: bool = False
        self.cooldown_until_bar: int = 0
        self.min_drift_bps: float = 35.0   # Clear directional drift required
        self.max_drift_bps: float = 350.0  # Avoid entering after blow-off moves

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce hard cooldown of 4 bars (~4 hours) post-exit to prevent whipsawing
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
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

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        if hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Hard exit: flat out before the next Asia session opens (UTC 22:00 - 23:00)
        if ctx.has_position():
            if hour >= 23 or hour == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": price}
                )
            return None

        # Guard: one trade per day, respects cooldown
        if self.traded_today or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Entry window: US-session open window (UTC 13:00 - 15:00)
        if 13 <= hour <= 15:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            drift_pct = (self.asia_close_price - self.asia_open_price) / self.asia_open_price
            drift_bps = drift_pct * 10000.0

            # Trend and volatility confirmation
            ema30 = self._ema(closes, 30)
            highs = ctx.highs(20)
            lows = ctx.lows(20)
            atr = self._atr(highs, lows, closes, 14)

            if ema30 is None or atr is None:
                return None

            # Regime filter: Skip crisis/meltdown
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Bullish Asia drift continuation setup
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps:
                # Require price to stay above trend and above Asia close for healthy continuation
                if price > ema30 and price >= self.asia_close_price:
                    self.traded_today = True

                    # Confidence scaled by drift strength relative to range
                    norm_drift = min(1.0, max(0.5, drift_bps / 150.0))
                    confidence = round(norm_drift, 2)

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_positive_drift_us_continuation",
                            "drift_bps": round(drift_bps, 1),
                            "asia_open": round(self.asia_open_price, 2),
                            "asia_close": round(self.asia_close_price, 2),
                            "ema30": round(ema30, 2),
                            "atr": round(atr, 2),
                            "hour": hour,
                            "price": price
                        }
                    )

        return None