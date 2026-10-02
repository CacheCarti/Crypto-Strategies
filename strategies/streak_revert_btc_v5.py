from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "Streak Exhaustion Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.max_hold_bars = 5
        self.cooldown_bars = 10
        self.cooldown_until = 0
        self.entry_bar = 0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.cooldown_until = ctx.bar_index + self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        closes = ctx.closes(warmup)
        opens = ctx.opens(warmup)
        if len(closes) < warmup or len(opens) < warmup:
            return None

        # Filter out extreme crisis regimes to prevent streak continuation traps
        if ctx.regime == "crisis":
            return None

        rsi_val = self._rsi(closes, 14)
        if rsi_val is None:
            return None

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long":
                # Exit on clear green snap or max time horizon
                if ctx.bar.close > ctx.bar.open:
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "long_green_bar_snap",
                            "bars_held": bars_held,
                            "close": ctx.bar.close,
                            "open": ctx.bar.open,
                            "rsi": rsi_val,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.60,
                        metadata={
                            "reason": "long_time_stop_exhaustion",
                            "bars_held": bars_held,
                            "rsi": rsi_val,
                        },
                    )

            elif direction == "short":
                # Exit on clear red snap or max time horizon
                if ctx.bar.close < ctx.bar.open:
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "short_red_bar_snap",
                            "bars_held": bars_held,
                            "close": ctx.bar.close,
                            "open": ctx.bar.open,
                            "rsi": rsi_val,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.60,
                        metadata={
                            "reason": "short_time_stop_exhaustion",
                            "bars_held": bars_held,
                            "rsi": rsi_val,
                        },
                    )

            return None

        # Cooldown guard after exit
        if ctx.bar_index < self.cooldown_until:
            return None

        # Count consecutive directional streaks
        down_streak = 0
        for o, c in zip(reversed(opens), reversed(closes)):
            if c < o:
                down_streak += 1
            else:
                break

        up_streak = 0
        for o, c in zip(reversed(opens), reversed(closes)):
            if c > o:
                up_streak += 1
            else:
                break

        # Long Entry: 6+ consecutive down bars with oversold RSI filter (< 38)
        if down_streak >= self.min_streak and rsi_val < 38.0:
            confidence = min(0.65 + 0.06 * (down_streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            self.cooldown_until = ctx.bar_index + self.cooldown_bars
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "down_streak_exhaustion_long",
                    "streak_count": down_streak,
                    "rsi": rsi_val,
                    "close": ctx.bar.close,
                    "open": ctx.bar.open,
                    "regime": ctx.regime,
                },
            )

        # Short Entry: 6+ consecutive up bars with overbought RSI filter (> 62)
        if up_streak >= self.min_streak and rsi_val > 62.0:
            confidence = min(0.65 + 0.06 * (up_streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            self.cooldown_until = ctx.bar_index + self.cooldown_bars
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "up_streak_exhaustion_short",
                    "streak_count": up_streak,
                    "rsi": rsi_val,
                    "close": ctx.bar.close,
                    "open": ctx.bar.open,
                    "regime": ctx.regime,
                },
            )

        return None