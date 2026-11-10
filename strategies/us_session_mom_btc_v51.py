from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0
        self.mom_period = 12
        self.atr_period = 14
        self.min_ret_bps = 45.0

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour
        in_pos = ctx.has_position()

        # Session forced exit prior to UTC 21:00 or end of window
        if in_pos:
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_flat", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # One position per calendar day constraint
        if self.last_traded_date == current_date:
            return None

        # Only evaluate entries in the first half of the US session (UTC 13 to 15)
        if not (13 <= hour <= 15):
            return None

        # Skip extreme crisis regimes to prevent toxic adverse flow
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 12-bar pre-session price direction
        p_now = closes[-1]
        p_ref = closes[-1 - self.mom_period]
        raw_return_bps = ((p_now - p_ref) / p_ref) * 10000.0

        # ATR based filter to ensure momentum is statistically significant
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or p_now <= 0:
            return None

        norm_vol_bps = (atr / p_now) * 10000.0
        threshold = max(self.min_ret_bps, norm_vol_bps * 0.4)

        if raw_return_bps > threshold:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.5 + (raw_return_bps / (threshold * 3.0)) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_12h_bps": round(raw_return_bps, 2),
                    "threshold_bps": round(threshold, 2),
                    "hour": hour
                }
            )

        if raw_return_bps < -threshold:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.5 + (abs(raw_return_bps) / (threshold * 3.0)) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_12h_bps": round(raw_return_bps, 2),
                    "threshold_bps": round(threshold, 2),
                    "hour": hour
                }
            )

        return None