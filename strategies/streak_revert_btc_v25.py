from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalEventStudy(Strategy):
    METADATA = {
        "name": "StreakReversalEventStudy",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        opens = ctx.opens(25)

        if len(closes) < 20 or len(opens) < 20:
            return None

        current_close = closes[-1]
        current_open = opens[-1]

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Event study exit: snap to opposite color or time stop
            opposite_bar = (pos_dir == "long" and current_close < current_open) or \
                           (pos_dir == "short" and current_close > current_open)

            if opposite_bar or bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "streak_reversal_exit",
                        "bars_held": bars_held,
                        "opposite_bar": opposite_bar,
                        "close": current_close
                    }
                )
            return None

        # Hard cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Exclude extreme meltdown or crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Count streak up to current bar
        up_streak = 0
        down_streak = 0

        # Check consecutive completed directional bars ending with current bar
        for i in range(len(closes) - 1, -1, -1):
            c = closes[i]
            o = opens[i]
            if c > o:
                if down_streak > 0:
                    break
                up_streak += 1
            elif c < o:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        rsi = self._rsi(closes, period=14)

        # Bullish exhaustion (consecutive down bars + RSI oversold confirmation)
        if down_streak >= self.min_streak and rsi < 42.0:
            confidence = min(0.60 + 0.08 * (down_streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_streak_exhaustion_reversal",
                    "streak_length": down_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        # Bearish exhaustion (consecutive up bars + RSI overbought confirmation)
        if up_streak >= self.min_streak and rsi > 58.0:
            confidence = min(0.60 + 0.08 * (up_streak - self.min_streak), 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_streak_exhaustion_reversal",
                    "streak_length": up_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close
                }
            )

        return None