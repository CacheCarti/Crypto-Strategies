from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionRevert(Strategy):
    METADATA = {
        "name": "BtcStreakExhaustionRevert",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,  # ~4 hours target holding period
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5            # 5+ consecutive bars required for exhaustion
        self.cooldown_bars = 4         # Mandatory cooldown between trades
        self.last_exit_bar = -100
        self.entry_bar_idx = -100
        self.max_hold_bars = 6         # Fixed time stop (6 hours)
        self.current_streak = 0        # > 0 for green bars, < 0 for red bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Determine candle color for current completed bar
        is_green = ctx.bar.close > ctx.bar.open
        is_red = ctx.bar.close < ctx.bar.open

        if is_green:
            self.current_streak = self.current_streak + 1 if self.current_streak > 0 else 1
        elif is_red:
            self.current_streak = self.current_streak - 1 if self.current_streak < 0 else -1
        else:
            self.current_streak = 0

        # Handle active position management (holding structure & opposite bar snap)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_idx

            # Exit criteria 1: Time stop reached
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held, "streak": self.current_streak}
                )

            # Exit criteria 2: Streak snap (first counter-color bar confirming turn)
            if pos_dir == "long" and is_green and bars_held >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_green_bounce_realized", "bars_held": bars_held, "close": ctx.bar.close}
                )

            if pos_dir == "short" and is_red and bars_held >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_red_flush_realized", "bars_held": bars_held, "close": ctx.bar.close}
                )

            return None

        # Cooldown guard to avoid churn and bleeding on friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate momentum oscillator to prevent fading strong persistent trends without stretch
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Down streak exhaustion -> Long mean-reversion
        # Requires 5+ consecutive down bars and oversold stretch (RSI < 40)
        if self.current_streak <= -self.min_streak and rsi < 40.0:
            streak_len = abs(self.current_streak)
            confidence = min(0.60 + 0.08 * (streak_len - self.min_streak), 0.95)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_snap",
                    "streak": streak_len,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        # Up streak exhaustion -> Short mean-reversion
        # Requires 5+ consecutive up bars and overbought stretch (RSI > 60)
        if self.current_streak >= self.min_streak and rsi > 60.0:
            streak_len = self.current_streak
            confidence = min(0.60 + 0.08 * (streak_len - self.min_streak), 0.95)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_snap",
                    "streak": streak_len,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index