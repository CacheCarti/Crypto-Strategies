from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation Event",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours holding until UTC 21:00
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce hard multi-bar cooldown after position exit
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.date() if hasattr(current_time, "date") else current_time.day
        hour = current_time.hour

        # 1. Mandatory Session Exit: Force flat at or after UTC 21
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # 2. Check Cooldown & Daily Limit (1 trade per day)
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_day == current_day:
            return None

        # 3. Precise Entry Window: Exactly at UTC 15 (after measuring 13:00 and 14:00 bars)
        if hour != 15:
            return None

        # Market regime filter: avoid meltdowns or severe crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Measure the 2-hour opening move: from bar index -2 open (UTC 13 open) to bar index -1 close (UTC 14 close)
        opens = ctx.opens(5)
        if len(opens) < 3:
            return None

        open_13 = opens[-3]      # Open of UTC 13 bar
        close_14 = closes[-2]     # Close of UTC 14 bar
        move = close_14 - open_13
        move_bps = (move / open_13) * 10000.0
        abs_move = abs(move)

        # Baseline filter: Opening move must exceed 0.75 * ATR to demonstrate clear directional impulse
        # But not exceed 2.6 * ATR to prevent entering on an exhausted, overextended climax
        min_move = 0.75 * atr
        max_move = 2.60 * atr

        if abs_move < min_move or abs_move > max_move:
            return None

        # Directional alignment: Trend regime check (if present, do not trade directly against it)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if move_bps > 0 and trend_regime != "bear":
            self.last_traded_day = current_day
            confidence = min(0.9, 0.55 + (abs_move / max_move) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "atr": round(atr, 2),
                    "open_13": open_13,
                    "close_14": close_14,
                    "price": ctx.bar.close
                }
            )

        if move_bps < 0 and trend_regime != "bull":
            self.last_traded_day = current_day
            confidence = min(0.9, 0.55 + (abs_move / max_move) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "atr": round(atr, 2),
                    "open_13": open_13,
                    "close_14": close_14,
                    "price": ctx.bar.close
                }
            )

        return None