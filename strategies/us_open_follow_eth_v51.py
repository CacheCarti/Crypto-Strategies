from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Session Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.median_window = 24

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_trade_day == current_day:
            return None

        # Entry window: Bar completed at 15:00 UTC (captures moves during 13:00 - 15:00 UTC)
        if current_hour != 15:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Two-hour session move: close at 15:00 UTC vs close at 13:00 UTC (closes[-3])
        two_hour_move = closes[-1] - closes[-3]
        two_hour_abs = abs(two_hour_move)

        # 24-bar median 2-hour range baseline
        two_bar_ranges = [abs(closes[i] - closes[i - 2]) for i in range(2, len(closes))]
        if len(two_bar_ranges) < self.median_window:
            return None
        sorted_ranges = sorted(two_bar_ranges[-self.median_window:])
        median_range = sorted_ranges[len(sorted_ranges) // 2]

        # Opening impulse must exceed recent median range to confirm directional intent
        if two_hour_abs < median_range or two_hour_abs < (0.6 * atr):
            return None

        # Guard against exhausted / overextended moves
        if two_hour_abs > 3.0 * atr:
            return None

        # Fast trend alignment via short EMA
        ema10 = sum(closes[-10:]) / 10.0

        if two_hour_move > 0 and closes[-1] > ema10:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "two_hour_move": two_hour_move,
                    "median_range": median_range,
                    "atr": atr,
                    "hour": current_hour
                }
            )

        if two_hour_move < 0 and closes[-1] < ema10:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "two_hour_move": two_hour_move,
                    "median_range": median_range,
                    "atr": atr,
                    "hour": current_hour
                }
            )

        return None