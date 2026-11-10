from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold max
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price = None
        self.asia_close_price = None
        self.min_drift_bps = 35.0         # Meaningful positive drift filter (>0.35%)
        self.cooldown_until_bar = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        current_date = ts.date()
        hour = ts.hour
        current_close = ctx.bar.close

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        if hour == 8:
            self.asia_close_price = current_close

        # Mandatory exit: Close open positions before the next Asia session begins (UTC 22:00 - 23:00)
        if ctx.has_position():
            if hour >= 22 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": current_close}
                )
            return None

        # Cooldown check
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # One trade per day limit
        if self.last_traded_date == current_date:
            return None

        # Trigger window: US-session open window (UTC 13:00 - 15:00)
        if 13 <= hour <= 15:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Trend alignment filter: current price above EMA-24
                ema24 = self._ema(closes, 24)
                if ema24 is None:
                    return None

                # Market condition check: avoid extreme crisis regimes
                regime = ctx.market.get("regime", "NORMAL")
                crisis_score = ctx.market.get("crisis_score", 0.0)
                if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.7:
                    return None

                # Entry condition: positive Asia drift, price above EMA, and current price maintaining strength
                if asia_drift_bps >= self.min_drift_bps and current_close > ema24:
                    highs = ctx.highs(20)
                    lows = ctx.lows(20)
                    atr = self._atr(highs, lows, closes, 14)
                    atr_val = atr if atr is not None else 0.0

                    self.last_traded_date = current_date
                    confidence = min(0.9, max(0.5, 0.5 + (asia_drift_bps / 400.0)))

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=220.0,
                        take_profit_bps=400.0,
                        horizon_seconds=28800,
                        metadata={
                            "reason": "asia_drift_us_continuation",
                            "asia_drift_bps": round(asia_drift_bps, 2),
                            "ema24": round(ema24, 2),
                            "atr": round(atr_val, 2),
                            "hour": hour
                        }
                    )

        return None