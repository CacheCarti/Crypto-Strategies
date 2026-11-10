from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "Streak Reversal BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

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
        warmup = self.METADATA["warmup_bars"]
        if ctx.bar_index < warmup:
            return None

        closes = ctx.closes(warmup)
        opens = ctx.opens(warmup)
        if len(closes) < warmup or len(opens) < warmup:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green = ctx.bar.close > ctx.bar.open
            is_red = ctx.bar.close < ctx.bar.open
            bars_held = ctx.bar_index - self.entry_bar

            # Snap exit: first opposing bar color or time-based exhaustion
            if pos_dir == "long" and (is_green or bars_held >= self.max_hold_bars):
                reason = "streak_snapped_green" if is_green else "max_hold_reached"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "bars_held": bars_held, "close": ctx.bar.close})
            elif pos_dir == "short" and (is_red or bars_held >= self.max_hold_bars):
                reason = "streak_snapped_red" if is_red else "max_hold_reached"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "bars_held": bars_held, "close": ctx.bar.close})
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown/crisis environments where mean reversion fails
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate consecutive directional bar streak
        streak = 0
        streak_direction = 0  # 1 for up bars, -1 for down bars

        # Inspect completed historical sequence up to the current bar
        for c, o in zip(reversed(closes), reversed(opens)):
            if c > o:
                if streak_direction == 1:
                    streak += 1
                elif streak_direction == 0:
                    streak_direction = 1
                    streak = 1
                else:
                    break
            elif c < o:
                if streak_direction == -1:
                    streak += 1
                elif streak_direction == 0:
                    streak_direction = -1
                    streak = 1
                else:
                    break
            else:
                break

        if streak < self.min_streak:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Long exhaustion: >= 5 consecutive red bars + oversold RSI confirmation
        if streak_direction == -1 and rsi < 36.0:
            confidence = min(0.60 + 0.08 * (streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_down_streak_reversion",
                    "streak_len": streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                    "regime": regime,
                },
            )

        # Short exhaustion: >= 5 consecutive green bars + overbought RSI confirmation
        if streak_direction == 1 and rsi > 64.0:
            confidence = min(0.60 + 0.08 * (streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_up_streak_reversion",
                    "streak_len": streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                    "regime": regime,
                },
            )

        return None