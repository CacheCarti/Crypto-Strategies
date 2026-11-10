from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 UTC to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        current_day = ts.toordinal()

        # Structural timed exit: close at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Only evaluate entry exactly at UTC 15:00 (after the 13:00 and 14:00 bars close)
        if current_hour != 15:
            return None

        # Hard constraint: one trade per calendar day
        if self.last_traded_day == current_day:
            return None

        # Regime safety: avoid entering during extreme crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Look at the 2-hour opening window (UTC 13:00 bar open to UTC 15:00 bar open/close of 14:00 bar)
        # closes[-1] is 14:00 bar close (15:00 snapshot), closes[-3] is bar prior to 13:00 bar
        p_start = closes[-3]
        p_now = closes[-1]
        session_move = p_now - p_start
        abs_move_bps = (abs(session_move) / p_start) * 10000.0

        # Dynamic range threshold: 14-period ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        atr_bps = (atr / p_now) * 10000.0
        # Require opening move to be directional and exceed 0.8 * recent 1h ATR,
        # but avoid exhaustion moves exceeding 3.5 * ATR
        if abs_move_bps < max(45.0, atr_bps * 0.8) or abs_move_bps > atr_bps * 3.5:
            return None

        # Volatility-adaptive stop loss: 1.6x 1h ATR in bps, bounded between 140 and 320 bps
        sl_bps = min(max(atr_bps * 1.6, 140.0), 320.0)
        tp_bps = sl_bps * 1.8  # Favorable structural 1:1.8 asymmetric risk profile

        direction = "long" if session_move > 0 else "short"
        confidence = min(0.9, 0.55 + (abs_move_bps / (atr_bps * 3.5)) * 0.35)

        self.last_traded_day = current_day
        self.entry_price = ctx.bar.close

        return ctx.signal(
            direction,
            confidence=round(confidence, 2),
            stop_loss_bps=round(sl_bps, 1),
            take_profit_bps=round(tp_bps, 1),
            horizon_seconds=21600,
            metadata={
                "reason": f"us_open_{direction}_continuation",
                "abs_move_bps": round(abs_move_bps, 1),
                "atr_bps": round(atr_bps, 1),
                "sl_bps": round(sl_bps, 1),
                "tp_bps": round(tp_bps, 1),
                "hour": current_hour,
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_price = 0.0