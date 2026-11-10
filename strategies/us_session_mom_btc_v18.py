from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Event",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.min_return_bps = 50.0  # Clear directional move threshold (0.5%)

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Hard session close: Flatten everything at or after 21:00 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Respect cooldown and strict 1 trade per day limit
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == current_day:
            return None

        # Entry window: strictly at US market open (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Avoid extreme crisis / meltdown market regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure 12-bar momentum (overnight/London session trend into US open)
        price_now = closes[-1]
        price_past = closes[-1 - self.lookback]
        mom_bps = ((price_now - price_past) / price_past) * 10000.0

        # ATR filter to ensure momentum is statistically significant relative to volatility
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        atr_bps = (atr / price_now) * 10000.0
        # Require momentum to be at least 0.8x the 14-period ATR to filter out churn
        min_move = max(self.min_return_bps, atr_bps * 0.8)

        if mom_bps > min_move:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.9, 0.5 + (abs(mom_bps) / 200.0) * 0.2)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "hour": current_hour,
                    "price": price_now
                }
            )

        if mom_bps < -min_move:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.9, 0.5 + (abs(mom_bps) / 200.0) * 0.2)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "hour": current_hour,
                    "price": price_now
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 4 bars after exit
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)