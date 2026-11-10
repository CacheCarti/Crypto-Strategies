from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "UsOpenFollowEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold (UTC 15 to 21)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Hard exit: flat at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Only evaluate entry exactly at UTC 15:00 (after the 13:00-15:00 opening window)
        if current_hour != 15:
            return None

        # Cooldown & strictly one trade per day check
        if ctx.bar_index < self.cooldown_until_bar or current_day == self.last_traded_day:
            return None

        # Avoid meltdown or extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime == "MELTDOWN":
            return None

        # Prior 2 bars cover UTC 13:00 and 14:00 (evaluating at 15:00)
        open_price = ctx.opens(3)[-2]  # Open of 13:00 bar
        eval_price = closes[-1]        # Close of 14:00 bar (prior bar just closed)
        move_pct = (eval_price - open_price) / open_price

        # Calculate median 2-hour bar range over the last 48 hours for scaling
        highs = ctx.highs(48)
        lows = ctx.lows(48)
        ranges = [(highs[i] - lows[i - 1]) / lows[i - 1] for i in range(1, len(highs))]
        abs_ranges = [abs(r) for r in ranges if r != 0]
        if not abs_ranges:
            return None
        med_range = statistics.median(abs_ranges)

        # Opening move must exceed median range to show commitment, but not exceed 3.5x to avoid overextension
        abs_move = abs(move_pct)
        if abs_move < (med_range * 1.1) or abs_move > (med_range * 3.5):
            return None

        # Fast RSI-14 to prevent buying overextended exhaustion moves
        diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
        gains = [max(d, 0.0) for d in diffs]
        losses = [max(-d, 0.0) for d in diffs]
        avg_gain = sum(gains) / 14.0
        avg_loss = sum(losses) / 14.0
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Directional entry
        if move_pct > 0 and rsi < 68.0:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pct": round(move_pct, 4),
                    "med_range": round(med_range, 4),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        if move_pct < 0 and rsi > 32.0:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pct": round(move_pct, 4),
                    "med_range": round(med_range, 4),
                    "rsi": round(rsi, 2),
                    "hour": current_hour
                }
            )

        return None