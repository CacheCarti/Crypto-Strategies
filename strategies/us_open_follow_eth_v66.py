from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 24
        self.last_traded_day = -1
        self.bars_since_exit = 10

    def _atr(self, highs, lows, closes, period=24):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1

        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        day_id = current_time.toordinal()

        # Exit mandatory by UTC 21:00 or end of US session
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "us_session_end_flatten",
                    "hour": current_hour,
                    "price": ctx.bar.close
                })
            return None

        # One trade per calendar day restriction & cooldown
        if day_id == self.last_traded_day or self.bars_since_exit < 3:
            return None

        # Look specifically at UTC 15 (completion of 13:00 and 14:00 opening bars)
        if current_hour != 15:
            return None

        # The opening window is the last 2 hours (13:00 to 15:00)
        # closes[-1] is 15:00 close; closes[-3] is 13:00 open / close of 12:00
        open_price = closes[-3]
        close_price = closes[-1]
        move = close_price - open_price

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Check move magnitude: meaningful impulse but not overextended
        move_magnitude = abs(move)
        min_move = 0.75 * atr
        max_move = 2.4 * atr

        if not (min_move <= move_magnitude <= max_move):
            return None

        # Regime / Crisis filter: avoid entering during extreme market dislocations
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        direction = "long" if move > 0 else "short"
        confidence = min(0.85, max(0.55, move_magnitude / (2.0 * atr)))

        self.last_traded_day = day_id

        return ctx.signal(
            direction,
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": f"us_open_{direction}_continuation",
                "move_usd": round(move, 2),
                "move_atr_ratio": round(move_magnitude / atr, 2),
                "atr": round(atr, 2),
                "hour": current_hour,
                "entry_price": ctx.bar.close
            }
        )