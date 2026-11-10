from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.entry_price = 0.0

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 40:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        hour = current_time.hour

        # Session Hard Exit: Close any open position at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                pos = ctx.position
                entry = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": hour,
                        "close": ctx.bar.close,
                        "entry": entry
                    }
                )
            return None

        # One trade per day enforcement
        if self.last_trade_day == current_day:
            return None

        # Entry window: Evaluate right after the 2-hour US open window (at UTC 15:00)
        if hour != 15:
            return None

        # Volatility & Momentum benchmarks
        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)
        if atr is None or rsi is None or atr <= 0.0:
            return None

        # Measure the 2-hour opening move: price at 15:00 vs price at 13:00 (closes[-1] vs closes[-3])
        open_move = closes[-1] - closes[-3]
        abs_move = abs(open_move)

        # Range condition: move must exceed 1.25x recent 1h ATR (strong conviction filter)
        # and not exceed 3.0x ATR (reject overextended exhaustion moves)
        if abs_move < 1.25 * atr or abs_move > 3.0 * atr:
            return None

        # Trend filter using short EMA
        ema20 = sum(closes[-20:]) / 20.0

        # Bullish Continuation Setup
        if open_move > 0 and closes[-1] > ema20 and 52.0 <= rsi <= 68.0:
            self.last_trade_day = current_day
            self.entry_price = ctx.bar.close
            confidence = min(1.0, 0.65 + (abs_move / atr) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "open_move": open_move,
                    "atr": atr,
                    "rsi": rsi,
                    "ema20": ema20,
                    "hour": hour
                }
            )

        # Bearish Continuation Setup
        if open_move < 0 and closes[-1] < ema20 and 32.0 <= rsi <= 48.0:
            self.last_trade_day = current_day
            self.entry_price = ctx.bar.close
            confidence = min(1.0, 0.65 + (abs_move / atr) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "open_move": open_move,
                    "atr": atr,
                    "rsi": rsi,
                    "ema20": ema20,
                    "hour": hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_price = 0.0