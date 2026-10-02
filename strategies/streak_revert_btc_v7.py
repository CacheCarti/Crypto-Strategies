from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalExhaustion(Strategy):
    METADATA = {
        "name": "Streak Reversal Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 20,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 4
        self.cooldown_bars = 2
        self.cooldown_until = 0
        self.entry_bar_index = 0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def _get_streak(self, opens, closes):
        if len(closes) < 2 or len(opens) < 2:
            return 0, 0
        last_dir = 1 if closes[-1] > opens[-1] else (-1 if closes[-1] < opens[-1] else 0)
        if last_dir == 0:
            return 0, 0
        count = 0
        for i in range(len(closes) - 1, -1, -1):
            c, o = closes[i], opens[i]
            cur_dir = 1 if c > o else (-1 if c < o else 0)
            if cur_dir == last_dir:
                count += 1
            else:
                break
        return last_dir, count

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi_val = self._rsi(closes, 14)
        streak_dir, streak_count = self._get_streak(opens, closes)
        pos_dir = ctx.position_direction()

        # Exit logic when in position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_index
            current_bar_dir = 1 if ctx.bar.close > ctx.bar.open else (-1 if ctx.bar.close < ctx.bar.open else 0)

            # Exit long on first green snap bar or max hold reached
            if pos_dir == "long" and (current_bar_dir == 1 or bars_held >= 5):
                self.cooldown_until = ctx.bar_index + self.cooldown_bars
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "streak_snapped_bounce" if current_bar_dir == 1 else "max_hold_exhaustion_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    }
                )

            # Exit short on first red snap bar or max hold reached
            if pos_dir == "short" and (current_bar_dir == -1 or bars_held >= 5):
                self.cooldown_until = ctx.bar_index + self.cooldown_bars
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "streak_snapped_pullback" if current_bar_dir == -1 else "max_hold_exhaustion_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    }
                )

            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until:
            return None

        # Entry logic: Consecutive streak exhaustion with loosened RSI boundaries
        if streak_count >= self.min_streak:
            base_conf = min(0.90, 0.60 + (streak_count - self.min_streak) * 0.08)

            # Long after sustained down streak (exhaustion)
            if streak_dir == -1 and rsi_val <= 55.0:
                self.entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=round(base_conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "down_streak_exhaustion_reversal",
                        "streak_count": streak_count,
                        "streak_dir": streak_dir,
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    }
                )

            # Short after sustained up streak (exhaustion)
            if streak_dir == 1 and rsi_val >= 45.0:
                self.entry_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=round(base_conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "up_streak_exhaustion_reversal",
                        "streak_count": streak_count,
                        "streak_dir": streak_dir,
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    }
                )

        return None