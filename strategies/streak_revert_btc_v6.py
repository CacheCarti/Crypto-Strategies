from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BTC Streak Exhaustion Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 10800,
        "warmup_bars": 20,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 4
        self.cooldown_bars = 2
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar_idx = -999

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
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        
        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 1
            curr_bar_green = ctx.bar.close > ctx.bar.open
            curr_bar_red = ctx.bar.close < ctx.bar.open

            # Exit Long on green candle (streak snap confirmation) or hold limit
            if pos_dir == "long":
                if curr_bar_green:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "long_streak_snap_green_candle",
                        "close": ctx.bar.close,
                        "open": ctx.bar.open,
                        "bars_held": bars_held
                    })
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "long_max_hold_timeout",
                        "close": ctx.bar.close,
                        "bars_held": bars_held
                    })

            # Exit Short on red candle (streak snap confirmation) or hold limit
            elif pos_dir == "short":
                if curr_bar_red:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "short_streak_snap_red_candle",
                        "close": ctx.bar.close,
                        "open": ctx.bar.open,
                        "bars_held": bars_held
                    })
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "short_max_hold_timeout",
                        "close": ctx.bar.close,
                        "bars_held": bars_held
                    })

            return None

        # Cooldown guard after position exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Count consecutive same-color bars ending at previous/current bar
        down_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c < o:
                down_streak += 1
            else:
                break

        up_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c > o:
                up_streak += 1
            else:
                break

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Long Entry: Down streak exhaustion with mild RSI guard
        if down_streak >= self.min_streak and rsi < 50.0:
            confidence = min(0.60 + (down_streak - self.min_streak) * 0.10, 0.95)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_reversal",
                    "streak_length": down_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                    "open": ctx.bar.open
                }
            )

        # Short Entry: Up streak exhaustion with mild RSI guard
        if up_streak >= self.min_streak and rsi > 50.0:
            confidence = min(0.60 + (up_streak - self.min_streak) * 0.10, 0.95)
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_reversal",
                    "streak_length": up_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close,
                    "open": ctx.bar.open
                }
            )

        return None