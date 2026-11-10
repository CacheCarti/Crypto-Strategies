from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 to 21:00 UTC)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.entry_bar = -1
        self.atr_period = 24

    def _atr(self, highs, lows, closes, period=24):
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        has_pos = ctx.has_position()

        # Session close rule: Flatten by UTC 21:00 regardless of state
        if has_pos and current_hour >= 21:
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "session_close_utc21",
                    "hour": current_hour,
                    "close": ctx.bar.close
                }
            )

        # Do not enter if we already hold a position, if crisis regime, or if traded today
        if has_pos:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Evaluated strictly at the end of the 13:00-15:00 UTC window (bar hour 15)
        # Bar hour 15 close corresponds to the completion of the 2-hour opening interval
        if current_hour != 15:
            return None

        if self.last_trade_day == current_day:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        # 2-hour opening move: price change from open of 14:00 (close of 2 bars ago) to current close
        # closes[-1] is 15:00 close; closes[-3] is 13:00 close (start of 14:00 bar)
        open_move = closes[-1] - closes[-3]
        abs_move = abs(open_move)

        # Opening move must exceed 1.1x 24h average hourly range to confirm clear directionality
        if abs_move < 1.1 * atr:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Guard against entering overextended exhaustion moves
        # Long entry: directional expansion up, but RSI not yet overextended (< 68)
        if open_move > 0 and 52.0 < rsi < 68.0:
            self.last_trade_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_atr_ratio": round(abs_move / atr, 2),
                    "open_move": round(open_move, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2)
                }
            )

        # Short entry: directional expansion down, but RSI not yet oversold (> 32)
        if open_move < 0 and 32.0 < rsi < 48.0:
            self.last_trade_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_atr_ratio": round(abs_move / atr, 2),
                    "open_move": round(open_move, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass