from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "BtcAsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (exit by 21:00 UTC)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -50

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        if len(closes) < 50:
            return None

        bar_time = ctx.bar.timestamp
        hour = bar_time.hour
        day_key = bar_time.toordinal()

        # Track Asia session open (UTC 00:00) and close (snapshot at UTC 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = ctx.bar.close

        # Mandatory exit before the next Asia session begins (at UTC 22:00 or 23:00)
        if ctx.has_position():
            if hour >= 22:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "bar_index": ctx.bar_index}
                )
            return None

        # Cooldown guard: must wait at least 5 bars after an exit
        if ctx.bar_index - self.last_exit_bar < 5:
            return None

        # One trade per day limit
        if self.last_trade_day == day_key:
            return None

        # Entry window: US session open/overlap (UTC 13:00 to 15:00)
        if 13 <= hour <= 15:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia session drift in bps
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Trend & Volatility filters to avoid choppy/whipsaw days
            ema30 = self._ema(closes, 30)
            atr = self._atr(highs, lows, closes, 14)
            if ema30 is None or atr is None or atr == 0:
                return None

            # Drift must be meaningfully positive (at least +25 bps, capped at +300 bps to avoid chasing blow-offs)
            # Price must also hold above EMA30 and above Asia close at the US open
            if 25.0 <= asia_drift_bps <= 300.0 and ctx.bar.close > ema30 and ctx.bar.close >= self.asia_close_price:
                # Avoid entering during crisis meltdown regimes
                market_regime = ctx.market.get("regime", "NORMAL")
                if market_regime in ("CRISIS", "MELTDOWN"):
                    return None

                # Confidence scaled by drift strength
                confidence = min(0.9, max(0.55, 0.55 + (asia_drift_bps / 500.0)))
                self.last_trade_day = day_key

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=380.0,
                    horizon_seconds=25200,
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "hour": hour,
                        "close": ctx.bar.close,
                        "ema30": round(ema30, 2),
                        "regime": market_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index