from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.max_streak = 9
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(20)
        opens = ctx.opens(20)

        if len(closes) < 18 or len(opens) < 18:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        bar_idx = ctx.bar_index

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = bar_idx - self.entry_bar

            if pos_dir == "long":
                if current_close > current_open or bars_held >= 4:
                    self.last_exit_bar = bar_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_streak_snap_or_timeout",
                            "bars_held": bars_held,
                            "close": current_close,
                            "open": current_open,
                        },
                    )
            elif pos_dir == "short":
                if current_close < current_open or bars_held >= 4:
                    self.last_exit_bar = bar_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_streak_snap_or_timeout",
                            "bars_held": bars_held,
                            "close": current_close,
                            "open": current_open,
                        },
                    )
            return None

        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        streak_down = 0
        streak_up = 0

        for c, o in zip(reversed(closes), reversed(opens)):
            if c < o:
                if streak_up > 0:
                    break
                streak_down += 1
            elif c > o:
                if streak_down > 0:
                    break
                streak_up += 1
            else:
                break

        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        if streak_down >= self.min_streak and rsi_val < 38.0:
            clamped_streak = min(streak_down, self.max_streak)
            confidence = 0.55 + 0.08 * (clamped_streak - self.min_streak)
            confidence = min(max(confidence, 0.55), 0.90)

            self.entry_bar = bar_idx
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_down": streak_down,
                    "rsi": round(rsi_val, 2),
                    "price": current_close,
                },
            )

        if streak_up >= self.min_streak and rsi_val > 62.0:
            clamped_streak = min(streak_up, self.max_streak)
            confidence = 0.55 + 0.08 * (clamped_streak - self.min_streak)
            confidence = min(max(confidence, 0.55), 0.90)

            self.entry_bar = bar_idx
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_up": streak_up,
                    "rsi": round(rsi_val, 2),
                    "price": current_close,
                },
            )

        return None