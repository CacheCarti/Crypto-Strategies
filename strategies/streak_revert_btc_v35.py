from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakReversal(Strategy):
    METADATA = {
        "name": "BtcStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 6
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

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

        if len(closes) < 25 or len(opens) < 25:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_color = 1 if current_close >= current_open else -1

        # Manage existing position: exit on first opposite bar or max hold time
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long" and current_color == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_streak_snapped_up_close", "bars_held": bars_held}
                )
            elif pos_dir == "short" and current_color == -1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_streak_snapped_down_close", "bars_held": bars_held}
                )

            if bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held}
                )
            return None

        # Cooldown guard to avoid churn and repetitive entries
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count consecutive same-color bars up to the current bar
        streak_len = 0
        streak_dir = 0
        for i in range(len(closes) - 1, -1, -1):
            bar_dir = 1 if closes[i] >= opens[i] else -1
            if streak_dir == 0:
                streak_dir = bar_dir
                streak_len = 1
            elif bar_dir == streak_dir:
                streak_len += 1
            else:
                break

        if streak_len < self.streak_threshold:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Avoid trading during extreme crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            return None

        # Down streak exhaustion -> Go Long
        if streak_dir == -1 and rsi <= 38.0:
            conf = min(0.55 + (streak_len - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "down_streak_exhaustion_reversal",
                    "streak_len": streak_len,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        # Up streak exhaustion -> Go Short
        if streak_dir == 1 and rsi >= 62.0:
            conf = min(0.55 + (streak_len - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "up_streak_exhaustion_reversal",
                    "streak_len": streak_len,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        return None