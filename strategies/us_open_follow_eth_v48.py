from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation Event Strategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6h (UTC 15 to UTC 21)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.entry_bar = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.date()
        hour = current_time.hour

        # Hard session exit: Close any open position at or after UTC 21
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "bar_index": ctx.bar_index}
                )
            return None

        # Hard cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # One trade per day constraint
        if self.last_traded_day == current_day:
            return None

        # Entry window: Evaluate specifically at UTC 15 (after UTC 13:00 - 15:00 opening window closes)
        if hour != 15:
            return None

        # Opening move: net change over the past 2 hours (bars: close[-3] to close[-1])
        open_ref_price = closes[-3]
        eval_price = closes[-1]
        move_pct = (eval_price - open_ref_price) / open_ref_price
        move_bps = abs(move_pct) * 10000.0

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or open_ref_price <= 0:
            return None

        # 2-hour median/benchmark move expectation derived from ATR
        expected_2h_move_bps = (atr * 1.25 / open_ref_price) * 10000.0

        # Trend filter using EMA 34
        ema34 = self._ema(closes, 34)
        if ema34 is None:
            return None

        # Filter out choppy/insignificant moves and overextended exhausted spikes
        # Require opening move to be substantial (> expected_2h_move_bps) but not overextended (< 2.8x)
        if move_bps < max(expected_2h_move_bps, 60.0) or move_bps > expected_2h_move_bps * 2.8:
            return None

        # Market regime filter from platform context
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long continuation condition
        if move_pct > 0 and eval_price > ema34:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 8
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_long_continuation",
                    "move_bps": round(move_bps, 2),
                    "expected_move_bps": round(expected_2h_move_bps, 2),
                    "ema34": round(ema34, 2),
                    "price": eval_price
                }
            )

        # Short continuation condition
        if move_pct < 0 and eval_price < ema34:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 8
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_short_continuation",
                    "move_bps": round(move_bps, 2),
                    "expected_move_bps": round(expected_2h_move_bps, 2),
                    "ema34": round(ema34, 2),
                    "price": eval_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown on position closure
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)