from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (UTC 15 to UTC 21)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median(self, values):
        if not values:
            return 0.0
        s = sorted(values)
        mid = len(s) // 2
        return (s[mid] if len(s) % 2 != 0 else (s[mid - 1] + s[mid]) / 2.0)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit intraday position by UTC 21
        if ctx.has_position():
            if current_hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Entry window: exactly evaluated at the end of the opening 2-hour bracket (evaluated at UTC 15)
        if current_hour != 15:
            return None

        # Prevent duplicate entries on the same day and enforce cooldown
        if current_day == self.last_traded_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Regime gate to avoid choppy failure modes: skip during crisis / extreme uncertainty
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Highs, lows, closes to evaluate recent ranges and current session move
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Opening move: from UTC 13 open (which corresponds to close of bar -2 or open of bar -1) to UTC 15 close
        # bar[-1] is 14:00-15:00, bar[-2] is 13:00-14:00. The open of UTC 13 is close of bar[-3].
        open_price_utc13 = closes[-3]
        current_close = closes[-1]
        session_move = current_close - open_price_utc13
        abs_session_move = abs(session_move)

        # Baseline recent 2-hour bar ranges to require opening expansion above recent median
        recent_2h_ranges = [abs(closes[i] - closes[i - 2]) for i in range(2, len(closes) - 1)]
        median_2h_range = self._median(recent_2h_ranges)

        # Conditions: clear directional impulse that exceeds recent median, but is not overextended (> 2.8 ATR)
        if abs_session_move < median_2h_range * 1.15:
            return None
        if abs_session_move > atr * 2.8:
            return None

        # Align with high-level trend regime to filter counter-trend fakeouts
        trend_regime = ctx.market.get("trend_regime", "neutral")

        direction = None
        if session_move > 0 and trend_regime != "bear":
            direction = "long"
        elif session_move < 0 and trend_regime != "bull":
            direction = "short"

        if direction is not None:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            confidence = min(0.9, max(0.55, abs_session_move / (atr * 2.0)))
            return ctx.signal(
                direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": f"us_open_continuation_{direction}",
                    "session_move": session_move,
                    "median_range": median_2h_range,
                    "atr": atr,
                    "trend_regime": trend_regime,
                    "crisis_score": crisis_score,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 4 bars following trade exit
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)