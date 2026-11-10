from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalExhaustion(Strategy):
    METADATA = {
        "name": "StreakReversalExhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 4
        self.entry_bar_idx = 0

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

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_bar_green = closes[-1] > opens[-1]
            curr_bar_red = closes[-1] < opens[-1]
            bars_held = ctx.bar_index - self.entry_bar_idx

            # Exit if snap occurs (opposite bar forms) or time budget expires
            if pos_dir == "long" and (curr_bar_green or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_streak_snap_or_timeout", "bars_held": bars_held}
                )
            elif pos_dir == "short" and (curr_bar_red or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_streak_snap_or_timeout", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive directional bars
        consecutive_down = 0
        consecutive_up = 0

        # Scan backwards from the current completed bar
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] < opens[i]:
                if consecutive_up > 0:
                    break
                consecutive_down += 1
            elif closes[i] > opens[i]:
                if consecutive_down > 0:
                    break
                consecutive_up += 1
            else:
                break

        rsi_val = self._rsi(closes, 14)
        if rsi_val is None:
            return None

        # Exhaustion Long: Streak of down bars accompanied by oversold momentum
        if consecutive_down >= self.min_streak and rsi_val <= 40.0:
            confidence = min(0.55 + (consecutive_down - self.min_streak) * 0.1, 0.90)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_count": consecutive_down,
                    "rsi": round(rsi_val, 2),
                    "close": closes[-1]
                }
            )

        # Exhaustion Short: Streak of up bars accompanied by overbought momentum
        if consecutive_up >= self.min_streak and rsi_val >= 60.0:
            confidence = min(0.55 + (consecutive_up - self.min_streak) * 0.1, 0.90)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_count": consecutive_up,
                    "rsi": round(rsi_val, 2),
                    "close": closes[-1]
                }
            )

        return None