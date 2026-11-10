from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # up to 6 hours (UTC 15 to 21)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.entry_bar_idx: Optional[int] = None

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_day = dt.toordinal()
        current_hour = dt.hour

        # Check timed exit: Close positions by UTC 21
        if ctx.has_position():
            if current_hour >= 21 or (self.entry_bar_idx is not None and ctx.bar_index - self.entry_bar_idx >= 6):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timed_exit", "hour": current_hour, "bars_held": ctx.bar_index - (self.entry_bar_idx or 0)}
                )
            return None

        # Reset daily trade state if new day
        if self.last_traded_day != current_day:
            self.entry_bar_idx = None

        # Entry window strictly evaluated at UTC 15 (after UTC 13:00 and 14:00 bars close)
        if current_hour != 15:
            return None

        # Gate: One trade per day
        if self.last_traded_day == current_day:
            return None

        # Skip crisis regimes
        if ctx.regime in ("crisis", "CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # The opening move over the last 2 hours (UTC 13-15):
        # closes[-1] is close of 14:00-15:00 bar, closes[-3] is open/close baseline before 13:00
        open_move = closes[-1] - closes[-3]
        abs_move = abs(open_move)
        open_high = max(highs[-2], highs[-1])
        open_low = min(lows[-2], lows[-1])
        session_range = open_high - open_low

        # Recent median 2-bar range over previous 20 bars
        two_bar_ranges = [abs(closes[i] - closes[i-2]) for i in range(len(closes)-22, len(closes)-2)]
        two_bar_ranges.sort()
        median_range = two_bar_ranges[len(two_bar_ranges) // 2] if two_bar_ranges else atr

        # Opening move must exceed recent median 2-bar range and at least 0.8 * ATR
        if abs_move < median_range or abs_move < (0.8 * atr):
            return None

        # Directional conviction: net move must be at least 60% of total session range (not choppy)
        if session_range <= 0 or (abs_move / session_range) < 0.60:
            return None

        # Overextension check: move shouldn't exceed 2.8 * ATR (exhaustion risk)
        if abs_move > 2.8 * atr:
            return None

        # Volatility-adjusted stop-loss & take-profit in basis points
        price = ctx.bar.close
        atr_bps = (atr / price) * 10000.0
        sl_bps = max(130.0, min(320.0, atr_bps * 1.5))
        tp_bps = max(200.0, min(500.0, atr_bps * 2.5))

        direction = "long" if open_move > 0 else "short"
        self.last_traded_day = current_day
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            direction,
            confidence=0.75,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=21600,
            metadata={
                "reason": f"us_open_{direction}_continuation",
                "open_move": round(open_move, 2),
                "median_range": round(median_range, 2),
                "atr": round(atr, 2),
                "sl_bps": round(sl_bps, 1),
                "tp_bps": round(tp_bps, 1),
                "hour": current_hour
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = None