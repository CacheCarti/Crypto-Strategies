from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StreakReversal(Strategy):
    METADATA = {
        "name": "Streak Reversal Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Crisis filter: skip extreme turmoil regimes
        if ctx.regime == "crisis":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_regime_exit", "bar_index": ctx.bar_index},
                )
            return None

        # Manage open position exit: exit on the first opposite-color bar (streak snapped)
        if ctx.has_position():
            direction = ctx.position_direction()
            bar = ctx.bar
            is_green = bar.close > bar.open
            is_red = bar.close < bar.open

            if direction == "long" and is_green:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_streak_snap_green_bar",
                        "close": bar.close,
                        "open": bar.open,
                        "bar_index": ctx.bar_index,
                    },
                )
            elif direction == "short" and is_red:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_streak_snap_red_bar",
                        "close": bar.close,
                        "open": bar.open,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Strict multi-bar cooldown between trades to reduce friction overhead
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        lookback = 20
        closes = ctx.closes(lookback)
        opens = ctx.opens(lookback)

        if len(closes) < lookback or len(opens) < lookback:
            return None

        # Count consecutive red / green candles backwards from latest completed bar
        down_streak = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] < opens[i]:
                down_streak += 1
            else:
                break

        up_streak = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] > opens[i]:
                up_streak += 1
            else:
                break

        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # 6+ consecutive down bars with RSI oversold confirmation -> Long Reversal
        if down_streak >= self.min_streak and rsi_val <= 38.0:
            confidence = min(0.90, 0.65 + (down_streak - self.min_streak) * 0.08)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "down_streak_exhaustion_rsi_confirmed",
                    "streak_length": down_streak,
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fg_index,
                    "close": ctx.bar.close,
                },
            )

        # 6+ consecutive up bars with RSI overbought confirmation -> Short Reversal
        if up_streak >= self.min_streak and rsi_val >= 62.0:
            confidence = min(0.90, 0.65 + (up_streak - self.min_streak) * 0.08)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "up_streak_exhaustion_rsi_confirmed",
                    "streak_length": up_streak,
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fg_index,
                    "close": ctx.bar.close,
                },
            )

        return None