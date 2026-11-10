from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class USSessionBreakoutContinuation(Strategy):
    METADATA = {
        "name": "USSessionBreakoutContinuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours (15:00 UTC to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar_idx = -1
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Hard intraday time exit: close by UTC 21:00 regardless
        if ctx.has_position():
            if current_hour >= 21 or (ctx.bar_index - self.entry_bar_idx >= 6):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc_21",
                        "current_hour": current_hour,
                        "bars_held": ctx.bar_index - self.entry_bar_idx,
                        "close_price": ctx.bar.close
                    }
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.entry_bar_idx < self.cooldown_bars:
            return None

        # One trade per day restriction
        if self.last_traded_day == current_day:
            return None

        # Evaluate setup strictly at UTC 15:00 (closing of the 13:00-15:00 opening window)
        # Bar at UTC 15:00 corresponds to price action after hours 13 and 14 have completed
        if current_hour != 15:
            return None

        # Recent median 2-bar range calculation to ensure significant opening impulse
        # Evaluate 2-bar absolute moves across the past 24 bars
        two_bar_ranges = [abs(closes[i] - closes[i - 2]) for i in range(2, len(closes) - 1)]
        if not two_bar_ranges:
            return None
        median_two_bar_move = statistics.median(two_bar_ranges)

        # Measure 13:00 to 15:00 net displacement (closes[-1] is 15:00 close, closes[-3] is 13:00 open/close)
        open_move = closes[-1] - closes[-3]
        abs_open_move = abs(open_move)

        # Volatility reference (ATR)
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None

        # Range condition: move must exceed recent median move and be at least 0.8 * ATR
        # Overextension condition: move must not exceed 2.6 * ATR
        if abs_open_move < max(median_two_bar_move * 1.1, atr_val * 0.8):
            return None
        if abs_open_move > atr_val * 2.6:
            return None

        # Avoid entries in extreme crisis or meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Directional impulse determination
        direction = "long" if open_move > 0 else "short"
        confidence = min(0.9, max(0.55, abs_open_move / (atr_val * 2.0)))

        self.last_traded_day = current_day
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            direction,
            confidence=round(confidence, 2),
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "us_open_continuation",
                "open_move": round(open_move, 2),
                "median_2bar_move": round(median_two_bar_move, 2),
                "atr": round(atr_val, 2),
                "regime": regime,
                "entry_hour": current_hour
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = ctx.bar_index