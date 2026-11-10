from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar = -1
        self.cooldown_until_bar = 0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        day_id = current_time.year * 1000 + current_time.timetuple().tm_yday

        # Mandatory close at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or (ctx.bar_index - self.entry_bar >= 7):
                self.cooldown_until_bar = ctx.bar_index + 4
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bars_held": ctx.bar_index - self.entry_bar}
                )
            return None

        # One trade per day restriction & cooldown
        if day_id == self.last_traded_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Evaluate continuation at UTC 15 (after 13:00 and 14:00 bars close)
        if current_hour != 15:
            return None

        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or atr_val <= 0:
            return None

        # 2-hour opening move: price change from start of 13:00 bar (close 2 bars back) to close of 14:00 bar (close 1 bar back)
        open_ref_price = closes[-3]
        current_price = ctx.bar.close
        move = current_price - open_ref_price
        abs_move = abs(move)

        # Dynamic range threshold: Opening move must exceed 1.25x recent 1h ATR
        if abs_move < 1.25 * atr_val:
            return None

        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        # Avoid extreme overextension: RSI must leave room for continuation
        if move > 0 and 45.0 < rsi_val < 72.0:
            self.last_traded_day = day_id
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_atr_ratio": round(abs_move / atr_val, 2),
                    "rsi": round(rsi_val, 2),
                    "price": current_price
                }
            )

        if move < 0 and 28.0 < rsi_val < 55.0:
            self.last_traded_day = day_id
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_atr_ratio": round(abs_move / atr_val, 2),
                    "rsi": round(rsi_val, 2),
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4