from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation Event",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 30:
            return None

        bar_time = ctx.bar.timestamp
        current_date = bar_time.date()
        hour = bar_time.hour

        # Hard intraday session close at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Check cooldown and one-trade-per-day condition
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.last_traded_date == current_date:
            return None

        # Market regime filter: avoid entering during extreme crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Event Trigger Window: Evaluated right after the 2-hour US open window (at UTC 15:00)
        if hour == 15:
            # Need at least 3 bars (bars UTC 13, 14, and current close at 15)
            # Closes[-3] is close prior to UTC 13 opening, closes[-1] is close of UTC 14 bar / start of 15
            open_price = closes[-3]
            recent_close = closes[-1]
            net_move = recent_close - open_price
            net_move_pct = (net_move / open_price) * 10000.0  # in bps

            atr = self._atr(highs, lows, closes, period=14)
            if atr is None or atr <= 0:
                return None

            atr_bps = (atr / recent_close) * 10000.0

            # Median/characteristic threshold: opening move must be directional (> 0.8 * ATR)
            # but not exhausted (< 2.5 * ATR)
            min_threshold_bps = max(35.0, 0.75 * atr_bps)
            max_threshold_bps = 2.5 * atr_bps

            abs_move = abs(net_move_pct)
            if min_threshold_bps <= abs_move <= max_threshold_bps:
                direction = "long" if net_move > 0 else "short"
                self.last_traded_date = current_date

                confidence = min(0.85, max(0.55, 0.5 + (abs_move / max_threshold_bps) * 0.35))

                return ctx.signal(
                    direction,
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=380.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": f"us_open_{direction}_continuation",
                        "net_move_bps": round(net_move_pct, 2),
                        "atr_bps": round(atr_bps, 2),
                        "hour": hour,
                        "price": ctx.bar.close,
                    }
                )

        return None