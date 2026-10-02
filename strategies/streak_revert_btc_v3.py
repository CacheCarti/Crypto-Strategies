from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StreakReversalExhaustion(Strategy):
    METADATA = {
        "name": "BTC Streak Reversal Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 10
        self.max_hold_bars = 6
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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _get_streak(self, closes, opens):
        if len(closes) < 2:
            return 0
        last_dir = 1 if closes[-1] > opens[-1] else (-1 if closes[-1] < opens[-1] else 0)
        if last_dir == 0:
            return 0
        count = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            d = 1 if c > o else (-1 if c < o else 0)
            if d == last_dir:
                count += 1
            else:
                break
        return count * last_dir

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        opens = ctx.opens(35)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        streak = self._get_streak(closes, opens)
        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Long exit: first green bar reversal or time stop
            if pos_dir == "long":
                if current_close > current_open:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "streak_snap_green_bar_exit",
                            "bars_held": bars_held,
                            "close": current_close,
                            "rsi": round(rsi, 2),
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "time_stop_exhaustion_long",
                            "bars_held": bars_held,
                            "close": current_close,
                            "rsi": round(rsi, 2),
                        },
                    )

            # Short exit: first red bar reversal or time stop
            elif pos_dir == "short":
                if current_close < current_open:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "streak_snap_red_bar_exit",
                            "bars_held": bars_held,
                            "close": current_close,
                            "rsi": round(rsi, 2),
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "time_stop_exhaustion_short",
                            "bars_held": bars_held,
                            "close": current_close,
                            "rsi": round(rsi, 2),
                        },
                    )

            return None

        # Hard multi-bar cooldown check after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes to prevent catching falling knives
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if ctx.regime == "crisis" or crisis_score > 0.65:
            return None

        # Entry logic: 6+ consecutive down bars with deep oversold RSI
        if streak <= -self.min_streak and rsi < 34.0:
            abs_streak = abs(streak)
            confidence = min(0.65 + (abs_streak - self.min_streak) * 0.08, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_long",
                    "streak_count": abs_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "crisis_score": round(crisis_score, 2),
                },
            )

        # Entry logic: 6+ consecutive up bars with overbought RSI
        if streak >= self.min_streak and rsi > 66.0:
            abs_streak = streak
            confidence = min(0.65 + (abs_streak - self.min_streak) * 0.08, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_short",
                    "streak_count": abs_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "crisis_score": round(crisis_score, 2),
                },
            )

        return None