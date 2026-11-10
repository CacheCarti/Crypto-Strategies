from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours (UTC 15 to UTC 20/21)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.entry_bar_index: Optional[int] = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4
        self.entry_bar_index = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit positions by UTC 21 or after max 6 bars
        if ctx.has_position():
            bars_in_trade = (ctx.bar_index - self.entry_bar_index) if self.entry_bar_index is not None else 0
            if current_hour >= 21 or bars_in_trade >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_timeout", "hour": current_hour, "bars_held": bars_in_trade}
                )
            return None

        # Guard: Check cooldown and ensure at most 1 trade attempt per calendar day
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == current_day:
            return None

        # Entry trigger strictly at UTC 15:00 (evaluates opening move from UTC 13:00 to 15:00)
        if current_hour != 15:
            return None

        highs = ctx.highs(35)
        lows = ctx.lows(35)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0.0:
            return None

        # Two-hour session move: price change from start of UTC 13 to end of UTC 14
        # At hour 15 open/bar, closes[-1] is 14:00 close, closes[-3] is 12:00 close (13:00 open equivalent)
        two_hour_move = closes[-1] - closes[-3]
        two_hour_range = max(highs[-2], highs[-1]) - min(lows[-2], lows[-1])

        # Require opening move to exceed recent ATR threshold (active session participation)
        if two_hour_range < 0.9 * atr:
            return None

        # Trend and exhaustion filters
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Dynamic directional decision
        # Move must be clearly directional: net move at least 65% of the 2-hour range
        is_bullish = two_hour_move > 0 and (two_hour_move / two_hour_range) >= 0.65
        is_bearish = two_hour_move < 0 and (abs(two_hour_move) / two_hour_range) >= 0.65

        # Avoid extreme exhaustion entries
        if is_bullish and rsi < 70.0 and two_hour_move > (0.6 * atr):
            self.last_traded_day = current_day
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pts": round(two_hour_move, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        if is_bearish and rsi > 30.0 and abs(two_hour_move) > (0.6 * atr):
            self.last_traded_day = current_day
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pts": round(two_hour_move, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        return None