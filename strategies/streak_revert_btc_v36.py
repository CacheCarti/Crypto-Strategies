from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalAdaptive(Strategy):
    METADATA = {
        "name": "StreakReversalAdaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # 4 hours average
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.min_streak = 5
        self.max_hold_bars = 6
        self.entry_bar = 0

    def _streak_length(self, opens, closes):
        if len(closes) < 2:
            return 0
        streak = 0
        direction = 1 if closes[-1] > opens[-1] else (-1 if closes[-1] < opens[-1] else 0)
        if direction == 0:
            return 0
        for o, c in zip(reversed(opens), reversed(closes)):
            cur_dir = 1 if c > o else (-1 if c < o else 0)
            if cur_dir == direction:
                streak += 1
            else:
                break
        return streak * direction

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(50)
        opens = ctx.opens(50)
        if not closes or not opens or len(closes) < 50:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        streak = self._streak_length(opens, closes)
        rsi = self._rsi(closes, 14)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Snap exit: exit on the first counter-color bar or time exhaustion
            is_green = current_close > current_open
            is_red = current_close < current_open

            if pos_dir == "long" and (is_red or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "streak_snap_long_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "streak": streak
                    }
                )
            elif pos_dir == "short" and (is_green or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "streak_snap_short_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "streak": streak
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entering during meltdown/crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Exhaustion Long Entry: Down streak >= 5 and RSI confirming stretched downside
        if streak <= -self.min_streak and rsi < 38.0:
            abs_streak = abs(streak)
            confidence = min(0.9, 0.55 + (abs_streak - self.min_streak) * 0.08)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downside_exhaustion_streak_snap",
                    "streak": streak,
                    "rsi": round(rsi, 2),
                    "price": current_close
                }
            )

        # Exhaustion Short Entry: Up streak >= 5 and RSI confirming stretched upside
        if streak >= self.min_streak and rsi > 62.0:
            confidence = min(0.9, 0.55 + (streak - self.min_streak) * 0.08)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upside_exhaustion_streak_snap",
                    "streak": streak,
                    "rsi": round(rsi, 2),
                    "price": current_close
                }
            )

        return None