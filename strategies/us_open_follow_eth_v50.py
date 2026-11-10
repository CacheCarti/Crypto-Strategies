from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class USOpenContinuation(Strategy):
    METADATA = {
        "name": "USOpenContinuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours (15:00 to 21:00 UTC)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        current_hour = current_time.hour

        # Hard exit rule: mandatory close at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "pos": pos_dir}
                )
            return None

        # Entry window: strictly trigger at UTC 15:00 after observing 13:00-15:00
        if current_hour != 15:
            return None

        # One trade per day restriction & cooldown
        if self.last_traded_date == current_date:
            return None
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Opening move: 2-hour window (13:00 to 15:00 close vs 13:00 open)
        # closes[-1] is 15:00 close, closes[-3] is 13:00 open approximation (close of 12:00)
        p_now = closes[-1]
        p_open = closes[-3]
        open_move_bps = ((p_now - p_open) / p_open) * 10000.0

        # Baseline: recent 24-hour typical 2-hour absolute moves
        two_bar_ranges = [abs(closes[i] - closes[i-2]) / closes[i-2] * 10000.0 for i in range(2, len(closes))]
        median_move_bps = statistics.median(two_bar_ranges)

        # Stricter event boundary: opening move must exceed 1.25x median move but not overextended (< 3.0x)
        min_threshold = max(40.0, median_move_bps * 1.25)
        max_threshold = median_move_bps * 3.0

        abs_move = abs(open_move_bps)
        if abs_move < min_threshold or abs_move > max_threshold:
            return None

        # Market crisis / extreme volatility avoidance
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.5:
            return None

        # Trend alignment check using 24h baseline (EMA24)
        ema24 = sum(closes[-24:]) / 24.0

        # Long Entry
        if open_move_bps > 0 and p_now > ema24:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "open_move_bps": round(open_move_bps, 1),
                    "median_move_bps": round(median_move_bps, 1),
                    "p_now": round(p_now, 2),
                    "ema24": round(ema24, 2)
                }
            )

        # Short Entry
        if open_move_bps < 0 and p_now < ema24:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "open_move_bps": round(open_move_bps, 1),
                    "median_move_bps": round(median_move_bps, 1),
                    "p_now": round(p_now, 2),
                    "ema24": round(ema24, 2)
                }
            )

        return None