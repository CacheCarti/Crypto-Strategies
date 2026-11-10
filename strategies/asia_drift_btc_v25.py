from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to ~20:00 UTC)
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price = None
        self.asia_close_price = None
        self.cooldown_until_bar = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 4 bars post-exit
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_date = dt.date()
        hour = dt.hour

        # Track Asia session (00:00 - 08:00 UTC) open and close
        if hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Time-based exit: Close before the next Asia session starts (e.g. at 21:00 UTC)
        if ctx.has_position():
            if hour >= 21 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Check entry gating: cooldown and strictly 1 trade per calendar day
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_date == current_date:
            return None

        # Only evaluate entry around US market open (13:00 - 15:00 UTC)
        if hour not in (13, 14):
            return None

        # Require a valid recorded Asia session
        if self.asia_open_price is None or self.asia_close_price is None or self.asia_open_price <= 0:
            return None

        asia_return_pct = (self.asia_close_price - self.asia_open_price) / self.asia_open_price

        # High-conviction threshold: Asia drift must be solidly positive (>= +0.35%)
        if asia_return_pct < 0.0035:
            return None

        # Market trend alignment: Price must be above medium-term EMA(40) to confirm macro tailwind
        ema_trend = self._ema(closes, 40)
        if ema_trend is None or ctx.bar.close <= ema_trend:
            return None

        # Volatility check: current drift must not be blown out (> 3.5% is likely overextended/exhaustion)
        if asia_return_pct > 0.035:
            return None

        # Check market regime isn't in full meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # ATR-based dynamic confidence
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        atr_ratio = (atr / ctx.bar.close) if (atr and ctx.bar.close > 0) else 0.005

        confidence = 0.65
        if asia_return_pct >= 0.008:
            confidence += 0.15
        if regime == "NORMAL":
            confidence += 0.05
        confidence = min(0.95, confidence)

        self.last_traded_date = current_date

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_drift_continuation_us_open",
                "asia_return_pct": round(asia_return_pct * 100, 3),
                "ema_trend": round(ema_trend, 2),
                "atr_ratio": round(atr_ratio, 4),
                "hour": hour,
                "price": ctx.bar.close
            }
        )