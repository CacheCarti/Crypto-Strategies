from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftBtc(Strategy):
    METADATA = {
        "name": "AsiaDriftBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours (13:00 to 21:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_drift_pct: float = 0.0
        self.current_day: Optional[int] = None
        self.traded_today: bool = False
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 4

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        day_key = ts.toordinal() if hasattr(ts, "toordinal") else ts.day
        hour = ts.hour

        # Reset daily state on date transition
        if self.current_day != day_key:
            self.current_day = day_key
            self.traded_today = False
            self.asia_open_price = None
            self.asia_drift_pct = 0.0

        # Snapshot start of Asia session at 00:00 UTC
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Timed exit: flatten before next Asia session kicks off (e.g., at 22:00 or 23:00 UTC)
        if ctx.has_position():
            if hour >= 22 or hour < 8:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timed_exit", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Record Asia drift by end of Asia session (hour 8)
        if 8 <= hour < 13:
            if self.asia_open_price is not None and self.asia_open_price > 0:
                self.asia_drift_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

        # Evaluation at US Session open (13:00 or 14:00 UTC)
        if 13 <= hour <= 15:
            if self.traded_today:
                return None
            if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
                return None
            if self.asia_open_price is None:
                return None

            # Calculate Asia drift if not already set
            drift = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

            # Structural regime & trend filter: price above 24h trend EMA
            ema_24 = self._ema(closes, 24)
            regime = ctx.market.get("regime", "NORMAL")
            
            # Avoid entries during systemic crisis/meltdown
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Meaningful positive drift (>0.30%) and price above EMA24
            if drift > 0.0030 and ema_24 is not None and ctx.bar.close > ema_24:
                atr = self._atr(highs, lows, closes, period=14)
                atr_bps = (atr / ctx.bar.close * 10000.0) if (atr and ctx.bar.close > 0) else 200.0

                # Volatility-adjusted stop & profit target
                sl_bps = max(140.0, min(320.0, atr_bps * 1.5))
                tp_bps = max(240.0, min(500.0, sl_bps * 1.8))
                conf = min(0.9, max(0.55, 0.5 + (drift * 20.0)))

                self.traded_today = True
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_positive_drift_us_continuation",
                        "asia_drift_pct": round(drift * 100.0, 3),
                        "ema_24": round(ema_24, 2),
                        "atr_bps": round(atr_bps, 1),
                        "price": ctx.bar.close,
                        "hour": hour,
                    }
                )

        return None