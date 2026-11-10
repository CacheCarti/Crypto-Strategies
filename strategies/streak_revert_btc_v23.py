from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _get_streak(self, opens, closes):
        """Returns positive int for consecutive green bars, negative for consecutive red bars."""
        if len(closes) < 2:
            return 0
        streak = 0
        is_green = closes[-1] > opens[-1]
        for i in range(len(closes) - 1, -1, -1):
            bar_green = closes[i] > opens[i]
            if bar_green == is_green:
                streak += 1 if is_green else -1
            else:
                break
        return streak

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        streak = self._get_streak(opens, closes)
        rsi = self._rsi(closes, period=14) or 50.0

        # Handle active position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_bar_green = closes[-1] > opens[-1]
            bars_held = ctx.bar_index - self.entry_bar

            # Snap exit: exit on the first opposite color bar once target minimum hold (1 bar) met
            if bars_held >= 1:
                if (pos_dir == "long" and not curr_bar_green) or (pos_dir == "short" and curr_bar_green):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "streak_reversal_snap_complete",
                            "bars_held": bars_held,
                            "streak": streak,
                            "rsi": round(rsi, 2)
                        }
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar <= self.cooldown_bars:
            return None

        # Regime safety filter (avoid counter-trend knife-catching during total crisis)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long entry: extended red streak exhaustion + RSI confirmation
        if streak <= -self.streak_threshold and rsi < 38.0:
            abs_streak = abs(streak)
            confidence = min(0.55 + (abs_streak - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_snap",
                    "consecutive_down_bars": abs_streak,
                    "rsi": round(rsi, 2),
                    "crisis_score": crisis_score
                }
            )

        # Short entry: extended green streak exhaustion + RSI overbought
        if streak >= self.streak_threshold and rsi > 62.0:
            confidence = min(0.55 + (streak - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_snap",
                    "consecutive_up_bars": streak,
                    "rsi": round(rsi, 2),
                    "crisis_score": crisis_score
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index