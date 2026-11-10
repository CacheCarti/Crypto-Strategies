from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours (UTC 15 to 20/21)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0

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
        current_date = ts.date()

        # Handle intraday time-based mandatory flat exit by UTC 21
        if ctx.has_position():
            if current_hour >= 21:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_utc21",
                    "hour": current_hour,
                    "close": ctx.bar.close
                })
            return None

        # Check cooldown and one-trade-per-day restriction
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_date == current_date:
            return None

        # We evaluate the opening move at UTC 15 (covering UTC 13:00 to 15:00, which is 2 full 1h bars)
        # Entry window strictly at hour 15
        if current_hour != 15:
            return None

        # Two-hour opening session move: close of 15:00 bar vs close of 13:00 bar (open of session)
        # closes[-1] is 14:00-15:00 close. closes[-3] is 12:00-13:00 close (approx session open).
        open_price = closes[-3]
        current_price = closes[-1]
        move = current_price - open_price
        move_pct = (move / open_price) * 100.0

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # Move requirement: opening move must exceed 1.1x ATR (meaningful range)
        # but not be overextended (less than 2.8x ATR)
        move_magnitude = abs(move)
        min_move = 1.1 * atr
        max_move = 2.8 * atr

        if not (min_move <= move_magnitude <= max_move):
            return None

        # Filter out extreme regime crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long continuation
        if move > 0:
            self.last_traded_date = current_date
            self.cooldown_until_bar = ctx.bar_index + 6
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pct": round(move_pct, 2),
                    "move_atr_ratio": round(move_magnitude / atr, 2),
                    "atr": round(atr, 2),
                    "price": current_price
                }
            )

        # Short continuation
        if move < 0:
            self.last_traded_date = current_date
            self.cooldown_until_bar = ctx.bar_index + 6
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pct": round(move_pct, 2),
                    "move_atr_ratio": round(move_magnitude / atr, 2),
                    "atr": round(atr, 2),
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce bar cooldown on exit
        self.cooldown_until_bar = ctx.bar_index + 4