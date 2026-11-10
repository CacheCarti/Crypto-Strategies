from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class USOpenMomentumContinuation(Strategy):
    METADATA = {
        "name": "US Open Momentum Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.entry_bar = -1
        self.cooldown_until_bar = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        opens = ctx.opens(40)

        if len(closes) < 40 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        current_dt = ctx.bar.timestamp
        current_date = current_dt.date()
        hour = current_dt.hour

        # Check for mandatory session exit at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close_timeout",
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Respect cooldown and strictly one trade per calendar day
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_date == current_date:
            return None

        # Stricter event boundary: trigger entry evaluation precisely at 15:00 UTC
        # This evaluates the two completed opening hours: 13:00-14:00 and 14:00-15:00.
        if hour != 15:
            return None

        # The completed 2-hour window corresponds to bar index -3 and -2 in context history
        # opens[-2] is the open at 13:00, closes[-1] is the close of the 14:00 bar (just completed)
        window_open = opens[-2]
        window_close = closes[-1]
        window_high = max(highs[-2], highs[-1])
        window_low = min(lows[-2], lows[-1])

        net_move = window_close - window_open
        window_range = window_high - window_low
        if window_range <= 0:
            return None

        # Compute median 2-bar range over the preceding 30 bars to ensure meaningful expansion
        two_bar_ranges = []
        for i in range(10, len(closes) - 2):
            seg_high = max(highs[i], highs[i+1])
            seg_low = min(lows[i], lows[i+1])
            two_bar_ranges.append(seg_high - seg_low)

        if not two_bar_ranges:
            return None

        med_range = statistics.median(two_bar_ranges)
        if window_range < med_range * 1.15:
            return None

        # Clean directional impulse: net move must be at least 65% of the total high-low span
        direction_ratio = abs(net_move) / window_range
        if direction_ratio < 0.65:
            return None

        # Check relative overextension: 2-hour move should not exceed 3.5%
        move_pct = (abs(net_move) / window_open) * 100.0
        if move_pct > 3.5:
            return None

        # Avoid trading during recognized crisis or high-volatility melt regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        confidence = min(0.9, 0.6 + (direction_ratio - 0.65) * 0.5)

        if net_move > 0:
            self.last_traded_date = current_date
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "direction_ratio": round(direction_ratio, 3),
                    "window_range": round(window_range, 2),
                    "med_range": round(med_range, 2),
                    "move_pct": round(move_pct, 2)
                }
            )
        elif net_move < 0:
            self.last_traded_date = current_date
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "direction_ratio": round(direction_ratio, 3),
                    "window_range": round(window_range, 2),
                    "med_range": round(med_range, 2),
                    "move_pct": round(move_pct, 2)
                }
            )

        return None