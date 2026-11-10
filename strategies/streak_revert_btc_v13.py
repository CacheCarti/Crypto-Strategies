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
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.max_hold_bars = 6
        self.entry_bar = 0

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
        if len(closes) < 20 or len(opens) < 20:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        bar_idx = ctx.bar_index

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = bar_idx - self.entry_bar

            # Exit condition 1: opposite-color bar indicates streak snapped
            if pos_dir == "long" and current_close < current_open:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_opposite_red_bar", "bars_held": bars_held, "close": current_close}
                )
            elif pos_dir == "short" and current_close > current_open:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_opposite_green_bar", "bars_held": bars_held, "close": current_close}
                )

            # Exit condition 2: time stop to avoid lingering in broken momentum
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "exit_time_stop", "bars_held": bars_held, "close": current_close}
                )

            return None

        # Cooldown guard after trade exit
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count consecutive same-color bars up to the current bar
        up_streak = 0
        down_streak = 0
        n = len(closes)
        for i in range(n - 1, -1, -1):
            if closes[i] > opens[i]:
                if down_streak > 0:
                    break
                up_streak += 1
            elif closes[i] < opens[i]:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Down streak exhaustion -> Mean reversion Long
        if down_streak >= self.min_streak and rsi < 38.0:
            confidence = min(0.55 + 0.08 * (down_streak - self.min_streak), 0.90)
            self.entry_bar = bar_idx
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "streak_down_exhaustion_reversal",
                    "down_streak": down_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        # Up streak exhaustion -> Mean reversion Short
        if up_streak >= self.min_streak and rsi > 62.0:
            confidence = min(0.55 + 0.08 * (up_streak - self.min_streak), 0.90)
            self.entry_bar = bar_idx
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "streak_up_exhaustion_reversal",
                    "up_streak": up_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        return None