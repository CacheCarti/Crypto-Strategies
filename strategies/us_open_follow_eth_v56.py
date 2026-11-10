from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
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
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        bar_time = ctx.bar.timestamp
        current_date = bar_time.date()
        hour = bar_time.hour

        # Exit intraday trades at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Hard guard: only evaluate an entry trigger at exactly UTC 15:00
        # (after the 13:00 and 14:00 bars complete the initial 2-hour US open window)
        if hour != 15:
            return None

        if self.last_traded_date == current_date:
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Filter out extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Two-hour window: open at bar index -2 (13:00 open) vs close at -1 (14:00 close)
        opens = ctx.opens(5)
        if len(opens) < 3:
            return None

        open_13 = opens[-2]
        close_14 = closes[-2]
        move = close_14 - open_13
        abs_move = abs(move)

        # Opening move must exceed 0.75 * ATR (meaningful momentum),
        # but not exceed 2.5 * ATR (avoid chasing exhausted blow-offs)
        if abs_move < (0.75 * atr) or abs_move > (2.5 * atr):
            return None

        # Check alignment with intermediate trend (24-bar SMA)
        sma24 = sum(closes[-24:]) / 24.0
        current_price = ctx.bar.close
        move_ratio = abs_move / atr

        if move > 0 and current_price >= sma24:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + 0.1 * move_ratio)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_ratio": round(move_ratio, 2),
                    "atr": round(atr, 2),
                    "price": current_price,
                    "sma24": round(sma24, 2)
                }
            )

        if move < 0 and current_price <= sma24:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + 0.1 * move_ratio)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_ratio": round(move_ratio, 2),
                    "atr": round(atr, 2),
                    "price": current_price,
                    "sma24": round(sma24, 2)
                }
            )

        return None