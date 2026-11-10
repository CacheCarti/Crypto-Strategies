from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold (UTC 15 to UTC 21)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.session_open_price = None

    def _atr(self, highs, lows, closes, period=24) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_dt = ctx.bar.timestamp
        current_day = current_dt.toordinal()
        current_hour = current_dt.hour

        # Hard session close by UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Track the start of US Open window (bar closing at 13:00 UTC captures open)
        if current_hour == 13:
            self.session_open_price = ctx.bar.open

        # Entry evaluated at bar closing at 15:00 UTC (covering 13:00 - 15:00 window)
        if current_hour == 15 and self.last_traded_day != current_day:
            if self.session_open_price is None:
                return None

            highs = ctx.highs(self.METADATA["warmup_bars"])
            lows = ctx.lows(self.METADATA["warmup_bars"])
            atr = self._atr(highs, lows, closes, period=24)
            if atr is None or atr <= 0:
                return None

            net_move = ctx.bar.close - self.session_open_price
            move_pct = net_move / self.session_open_price
            atr_ratio = abs(net_move) / atr

            # opening move must show clear momentum (> 0.75 ATR) but not be exhausted (< 2.2 ATR)
            # and require minimum directional displacement of 40 bps
            if 0.75 <= atr_ratio <= 2.2 and abs(move_pct) >= 0.0040:
                # Regime safety filter: don't chase into crisis meltdown
                regime = ctx.market.get("regime", "NORMAL")
                if regime in ("CRISIS", "MELTDOWN"):
                    return None

                self.last_traded_day = current_day
                direction = "long" if net_move > 0 else "short"
                confidence = min(0.85, max(0.55, atr_ratio / 2.0))

                return ctx.signal(
                    direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": f"us_open_trend_continuation_{direction}",
                        "atr_ratio": round(atr_ratio, 2),
                        "move_pct": round(move_pct, 4),
                        "atr": round(atr, 2),
                        "open_price": round(self.session_open_price, 2),
                        "close_price": round(ctx.bar.close, 2)
                    }
                )

        return None