from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.streak_dir = 0
        self.streak_len = 0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        if losses == 0.0:
            return 100.0
        rs = (gains / period) / (losses / period)
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < 30:
            return None

        bar = ctx.bar
        is_up = bar.close > bar.open
        is_down = bar.close < bar.open

        if is_up:
            if self.streak_dir == 1:
                self.streak_len += 1
            else:
                self.streak_dir = 1
                self.streak_len = 1
        elif is_down:
            if self.streak_dir == -1:
                self.streak_len += 1
            else:
                self.streak_dir = -1
                self.streak_len = 1
        else:
            self.streak_len = 0
            self.streak_dir = 0

        # Position exit logic: snap-back bar (opposite color) or time stop
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Snap back triggered or max bars held reached
            snap_exit = (pos_dir == "long" and is_up) or (pos_dir == "short" and is_down)
            time_exit = bars_held >= self.max_hold_bars
            
            if snap_exit or time_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "streak_snap_exit" if snap_exit else "max_hold_expired"
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": reason,
                    "bars_held": bars_held,
                    "close": bar.close
                })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading pure meltdown cascades
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        rsi = self._rsi(closes, 14)

        # Long Entry: Extended down-streak + exhaustion filter
        if self.streak_dir == -1 and self.streak_len >= self.min_streak:
            if rsi < 38.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.90, 0.55 + (self.streak_len - self.min_streak) * 0.08)
                return ctx.signal("long", confidence=conf, metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_len": self.streak_len,
                    "rsi": round(rsi, 2),
                    "close": bar.close
                })

        # Short Entry: Extended up-streak + exhaustion filter
        if self.streak_dir == 1 and self.streak_len >= self.min_streak:
            if rsi > 62.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.90, 0.55 + (self.streak_len - self.min_streak) * 0.08)
                return ctx.signal("short", confidence=conf, metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_len": self.streak_len,
                    "rsi": round(rsi, 2),
                    "close": bar.close
                })

        return None