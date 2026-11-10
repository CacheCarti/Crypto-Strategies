from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5          # 5+ consecutive same-direction bars
        self.cooldown_period = 6           # Bars to wait after closing a position
        self.max_hold_bars = 6             # Time-based stop to avoid stale holds
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < 20 or len(opens) < 20:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            current_bar_green = ctx.bar.close > ctx.bar.open
            current_bar_red = ctx.bar.close < ctx.bar.open

            # Exit on snap confirmation (first opposite color bar) or time exhaustion
            should_flat = False
            reason = ""
            if pos_dir == "long" and current_bar_green:
                should_flat = True
                reason = "streak_snapped_green_reversal"
            elif pos_dir == "short" and current_bar_red:
                should_flat = True
                reason = "streak_snapped_red_reversal"
            elif bars_held >= self.max_hold_bars:
                should_flat = True
                reason = "max_hold_bars_exhausted"

            if should_flat:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": reason, "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard after last exit to ensure independent setups
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Count active streak of consecutive up/down bars
        streak = 0
        direction = None  # "up" or "down"
        
        # Traverse backwards from the most recent completed bar
        for i in range(len(closes) - 1, -1, -1):
            bar_dir = "up" if closes[i] > opens[i] else ("down" if closes[i] < opens[i] else "doji")
            if bar_dir == "doji":
                break
            if direction is None:
                direction = bar_dir
                streak = 1
            elif direction == bar_dir:
                streak += 1
            else:
                break

        # Filter out extreme regime crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, period=14)

        # Reversal signals: Streak threshold + non-contradictory momentum exhaustion
        if streak >= self.streak_threshold and direction == "down":
            if rsi < 42.0:  # Confirms extended downward momentum ready for bounce
                conf = min(0.90, 0.55 + (streak - self.streak_threshold) * 0.10)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=350.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_down_streak_reversal",
                        "streak_length": streak,
                        "rsi": round(rsi, 2),
                        "close": ctx.bar.close
                    }
                )

        elif streak >= self.streak_threshold and direction == "up":
            if rsi > 58.0:  # Confirms extended upward momentum ready for pullback
                conf = min(0.90, 0.55 + (streak - self.streak_threshold) * 0.10)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=350.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_up_streak_reversal",
                        "streak_length": streak,
                        "rsi": round(rsi, 2),
                        "close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index