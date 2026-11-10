from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 3 bars post-exit to avoid re-triggering
        self.cooldown_until = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)
        if len(closes) < 48:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Exit existing positions by UTC 21:00 or later
        if ctx.has_position():
            if hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Check cooldown and max 1 trade per calendar day
        if ctx.bar_index < self.cooldown_until:
            return None
        if self.last_traded_date == current_date:
            return None

        # Regime gate to address prior OOS failures: avoid extreme crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry window: Bar closing at UTC 15:00 (evaluates opening move from UTC 13:00 to 15:00)
        if hour != 15:
            return None

        # Two-hour session window: index -3 is close at 13:00 (start of session), index -1 is close at 15:00
        p_start = closes[-3]
        p_end = closes[-1]
        session_move = p_end - p_start
        abs_session_move = abs(session_move)

        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or atr_val <= 0:
            return None

        # Require opening move to be directional and exceed recent 2-hour median/ATR threshold (0.8 ATR)
        # but not overextended (< 2.2 ATR) to avoid buying blow-off tops or exhaustion moves
        if abs_session_move < 0.8 * atr_val or abs_session_move > 2.2 * atr_val:
            return None

        # Directional continuation check
        direction = "long" if session_move > 0 else "short"
        confidence = min(0.9, max(0.55, abs_session_move / (2.0 * atr_val)))

        self.last_traded_date = current_date

        return ctx.signal(
            direction,
            confidence=confidence,
            stop_loss_bps=220.0,
            take_profit_bps=380.0,
            horizon_seconds=21600,
            metadata={
                "reason": f"us_open_{direction}_continuation",
                "session_move": session_move,
                "atr": atr_val,
                "regime": regime,
                "p_start": p_start,
                "p_end": p_end
            }
        )