from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_date = (current_time.year, current_time.month, current_time.day)
        current_hour = current_time.hour

        # Exit intraday trades at or after UTC 21
        if ctx.has_position():
            if current_hour >= 21 or (ctx.bar_index - self.entry_bar) >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_timeout", "hour": current_hour, "bars_held": ctx.bar_index - self.entry_bar}
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Only one setup evaluation per day at entry window (15:00 UTC)
        if current_hour != 15:
            return None

        if self.last_traded_date == current_date:
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, 24)
        if atr is None or atr <= 0.0:
            return None

        # Opening 2-hour window: 13:00 to 15:00 UTC
        # closes[-1] is 15:00 close; closes[-3] is 13:00 open (start of the 2-hour period)
        price_open_13 = ctx.opens(3)[0]
        price_now = closes[-1]
        session_move = price_now - price_open_13

        # Window ranges over recent days to compute percentile/median range
        recent_ranges = [highs[i] - lows[i] for i in range(-24, 0)]
        sorted_ranges = sorted(recent_ranges)
        median_range = sorted_ranges[len(sorted_ranges) // 2]

        abs_move = abs(session_move)
        # Entry logic: Move must exceed median hourly range, and be bounded by 2.5 * ATR (not blown out/exhausted)
        if abs_move < median_range or abs_move > (2.5 * atr):
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Bullish continuation: directional move up, not overbought (RSI between 52 and 72)
        if session_move > 0 and 52.0 <= rsi <= 72.0:
            self.last_traded_date = current_date
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "session_move": session_move,
                    "median_range": median_range,
                    "atr": atr,
                    "rsi": rsi,
                }
            )

        # Bearish continuation: directional move down, not oversold (RSI between 28 and 48)
        if session_move < 0 and 28.0 <= rsi <= 48.0:
            self.last_traded_date = current_date
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "session_move": session_move,
                    "median_range": median_range,
                    "atr": atr,
                    "rsi": rsi,
                }
            )

        return None