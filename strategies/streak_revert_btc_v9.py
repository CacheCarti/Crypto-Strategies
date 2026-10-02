from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversal(Strategy):
    METADATA = {
        "name": "Streak Reversal Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def _get_streak(self, opens: list, closes: list) -> tuple:
        """Returns ('down'|'up'|'neutral', streak_count)"""
        count = 0
        direction = None
        for i in range(len(closes) - 1, -1, -1):
            o = opens[i]
            c = closes[i]
            if c > o:
                current_dir = "up"
            elif c < o:
                current_dir = "down"
            else:
                break

            if direction is None:
                direction = current_dir
                count = 1
            elif direction == current_dir:
                count += 1
            else:
                break
        return (direction if direction else "neutral", count)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check market regime to avoid entering during extreme market meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "market_regime": market_regime},
                )
            return None

        streak_dir, streak_len = self._get_streak(opens, closes)
        rsi_val = self._rsi(closes, 14) or 50.0

        # Position Management & Exit Check
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            current_bar_green = ctx.bar.close > ctx.bar.open
            current_bar_red = ctx.bar.close < ctx.bar.open

            # Allow at least 1 bar to work; exit on snap candle or max hold time
            if pos_dir == "long" and (bars_held >= 1 and current_bar_green or bars_held >= 5):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_streak_snap_or_timeout",
                        "bars_held": bars_held,
                        "current_close": ctx.bar.close,
                        "streak_dir": streak_dir,
                        "rsi": round(rsi_val, 2),
                    },
                )
            elif pos_dir == "short" and (bars_held >= 1 and current_bar_red or bars_held >= 5):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_streak_snap_or_timeout",
                        "bars_held": bars_held,
                        "current_close": ctx.bar.close,
                        "streak_dir": streak_dir,
                        "rsi": round(rsi_val, 2),
                    },
                )
            return None

        # Hard Cooldown guard after exits to control trade count
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: High-conviction exhaustion with strict streak length + RSI confluence
        if streak_dir == "down" and streak_len >= self.min_streak and rsi_val <= 36.0:
            conf = min(0.60 + (streak_len - self.min_streak) * 0.08, 0.88)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_streak_exhaustion_long",
                    "streak_len": streak_len,
                    "rsi": round(rsi_val, 2),
                    "price": ctx.bar.close,
                },
            )

        elif streak_dir == "up" and streak_len >= self.min_streak and rsi_val >= 64.0:
            conf = min(0.60 + (streak_len - self.min_streak) * 0.08, 0.88)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_streak_exhaustion_short",
                    "streak_len": streak_len,
                    "rsi": round(rsi_val, 2),
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999