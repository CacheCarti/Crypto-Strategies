from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_date = bar_time.date()

        # Session close rule: flatten any open position at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "us_session_end_flatten",
                    "hour": current_hour,
                    "close": ctx.bar.close
                })
            return None

        # Check cooldown and one-trade-per-day constraint
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_date == current_date:
            return None

        # Only evaluate entry at the end of the 2-hour opening window (UTC 15:00)
        if current_hour != 15:
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0.0:
            return None

        # 2-hour opening window move: from UTC 13:00 open (2 bars back) to UTC 15:00 close (ctx.bar.close)
        opens = ctx.opens(5)
        open_13 = opens[-2]
        close_15 = ctx.bar.close
        move = close_15 - open_13
        move_magnitude = abs(move)

        # Baseline range hurdle: opening move must exceed 0.8 * ATR
        if move_magnitude < 0.80 * atr:
            return None

        # Overextension hurdle: move cannot exceed 2.4 * ATR
        if move_magnitude > 2.40 * atr:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Crisis avoidance
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Directional follow-through triggers with RSI overbought/oversold boundaries
        if move > 0 and rsi < 68.0:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pts": round(move, 2),
                    "move_atr_ratio": round(move_magnitude / atr, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        if move < 0 and rsi > 32.0:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pts": round(move, 2),
                    "move_atr_ratio": round(move_magnitude / atr, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        return None