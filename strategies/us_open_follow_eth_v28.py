from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -50
        self.atr_period = 24
        self.min_move_atr_mult = 0.8
        self.max_move_atr_mult = 2.5
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period=24):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_day = dt.toordinal()
        current_hour = dt.hour

        # Exit intraday trades at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timeout", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Cooldown guard: wait after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only evaluate entries right after the opening two-hour window at UTC 15
        if current_hour != 15:
            return None

        # One trade per day limit
        if self.last_trade_day == current_day:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Window covers 13:00 to 15:00 UTC (last 2 completed 1-hour bars: indices -2 and -1)
        # Entry price reference is open of 13:00 bar (ctx.opens(3)[-2])
        opens = ctx.opens(5)
        open_13 = opens[-2]
        close_15 = ctx.bar.close

        opening_move = close_15 - open_13
        abs_move = abs(opening_move)

        min_required = self.min_move_atr_mult * atr
        max_allowed = self.max_move_atr_mult * atr

        # Require opening move to be directional and not overextended
        if abs_move < min_required or abs_move > max_allowed:
            return None

        # Trend filter using market regime or return context
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.regime

        # Avoid entries during crisis regimes
        if regime in ("volatile", "crisis"):
            return None

        move_ratio = abs_move / atr
        confidence = min(0.9, max(0.5, 0.5 + (move_ratio - self.min_move_atr_mult) * 0.2))

        if opening_move > 0:
            if trend_regime == "bear":
                return None
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_ratio": move_ratio,
                    "atr": atr,
                    "opening_move": opening_move,
                    "hour": current_hour
                }
            )
        else:
            if trend_regime == "bull":
                return None
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_ratio": move_ratio,
                    "atr": atr,
                    "opening_move": opening_move,
                    "hour": current_hour
                }
            )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index