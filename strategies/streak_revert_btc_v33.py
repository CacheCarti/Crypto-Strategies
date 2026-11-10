from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 4

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < 30 or len(opens) < 30:
            return None

        # Check existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_pos = ctx.bar_index - self.entry_bar
            curr_close = closes[-1]
            curr_open = opens[-1]

            # Snap exit: exit on the first opposite-color bar or time exhaustion
            opposite_bar = (pos_dir == "long" and curr_close < curr_open) or \
                           (pos_dir == "short" and curr_close > curr_open)
            time_exhaust = bars_in_pos >= self.max_hold_bars

            if opposite_bar or time_exhaust:
                reason = "streak_snap_opposite_bar" if opposite_bar else "streak_hold_exhaustion"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": reason, "bars_held": bars_in_pos, "close": curr_close, "open": curr_open}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count streak length of consecutive same-color bars
        streak = 0
        streak_type = None  # "up" or "down"
        for i in range(len(closes) - 1, -1, -1):
            c = closes[i]
            o = opens[i]
            bar_color = "up" if c > o else ("down" if c < o else "flat")
            if bar_color == "flat":
                break
            if streak_type is None:
                streak_type = bar_color
                streak = 1
            elif streak_type == bar_color:
                streak += 1
            else:
                break

        if streak < self.min_streak:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Streak exhaustion: filter false breaks by ensuring oversold/overbought extremity
        # Prior evidence failed from premature fading into strong momentum trends; require RSI confirmation
        if streak_type == "down" and rsi < 36.0:
            conf = min(0.55 + (streak - self.min_streak) * 0.1, 0.90)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "consecutive_bars": streak,
                    "rsi": rsi,
                    "close": closes[-1]
                }
            )

        if streak_type == "up" and rsi > 64.0:
            conf = min(0.55 + (streak - self.min_streak) * 0.1, 0.90)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "consecutive_bars": streak,
                    "rsi": rsi,
                    "close": closes[-1]
                }
            )

        return None