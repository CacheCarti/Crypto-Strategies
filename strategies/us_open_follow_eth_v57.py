from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 UTC to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.entry_price = 0.0

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
        if len(closes) < 30:
            return None

        current_dt = ctx.bar.timestamp
        current_day = (current_dt.year, current_dt.month, current_dt.day)
        hour = current_dt.hour

        # Hard timed exit at or after 21:00 UTC
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timed", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Only evaluate entry precisely at 15:00 UTC (closing of the 13:00-15:00 window)
        if hour != 15:
            return None

        # Strictly enforce one trade per calendar day
        if self.last_traded_day == current_day:
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)

        # Baseline 2-hour opening move: price at bar 15 close vs 2 bars ago (price at 13 open)
        open_2h_ago = ctx.opens(3)[0]
        move = ctx.bar.close - open_2h_ago
        abs_move = abs(move)

        # Calculate median 2-bar range over past 24 bars to ensure significant impulse
        ranges_2bar = [abs(closes[i] - ctx.opens(24)[i - 2]) for i in range(2, 24)]
        median_range = statistics.median(ranges_2bar)

        if abs_move < 1.15 * median_range:
            return None

        # Check for overextension via ATR multiple
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Overextended move (> 2.8 ATR in 2 hours) often mean-reverts instead of continuing
        if abs_move > 2.8 * atr:
            return None

        # Dynamic volatility-based stop structure
        vol_sl_bps = max(140.0, min(300.0, (1.6 * atr / ctx.bar.close) * 10000.0))
        vol_tp_bps = vol_sl_bps * 1.6

        self.last_traded_day = current_day
        direction = "long" if move > 0 else "short"
        confidence = 0.7 if abs_move >= 1.5 * median_range else 0.55

        return ctx.signal(
            direction,
            confidence=confidence,
            stop_loss_bps=vol_sl_bps,
            take_profit_bps=vol_tp_bps,
            metadata={
                "reason": f"us_open_breakout_{direction}",
                "move": round(move, 2),
                "median_range": round(median_range, 2),
                "atr": round(atr, 2),
                "sl_bps": round(vol_sl_bps, 1),
                "tp_bps": round(vol_tp_bps, 1),
                "hour": hour
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass