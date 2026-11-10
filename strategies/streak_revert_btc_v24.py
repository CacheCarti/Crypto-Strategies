from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalVolatility(Strategy):
    METADATA = {
        "name": "StreakReversalVolatility",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # 4 hours intended
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.max_hold_bars = 5

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = 25
        closes = ctx.closes(n_bars)
        opens = ctx.opens(n_bars)
        highs = ctx.highs(n_bars)
        lows = ctx.lows(n_bars)

        if len(closes) < n_bars:
            return None

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            current_bar_green = closes[-1] > opens[-1]
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            # Structural exhaustion exit: snap candle achieved
            if pos_dir == "long" and current_bar_green:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exhaustion_snap_green_exit",
                    "bars_held": bars_held,
                    "close": closes[-1]
                })
            elif pos_dir == "short" and not current_bar_green:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exhaustion_snap_red_exit",
                    "bars_held": bars_held,
                    "close": closes[-1]
                })

            # Time stop exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_stop_exhaustion",
                    "bars_held": bars_held
                })
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading strong systemic crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Count streak on the most recent completed sequence
        up_streak = 0
        down_streak = 0

        # Scan backwards up to 12 bars
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] > opens[i]:
                if down_streak > 0:
                    break
                up_streak += 1
            elif closes[i] < opens[i]:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        # Calculate dynamic ATR stops
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None

        atr_bps = (atr / closes[-1]) * 10000.0
        stop_loss_bps = max(120.0, min(350.0, atr_bps * 1.5))
        take_profit_bps = max(180.0, min(500.0, atr_bps * 2.2))

        # Reversal triggers
        if down_streak >= self.min_streak:
            confidence = min(0.9, 0.55 + (down_streak - self.min_streak) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_reversal_down_exhaustion",
                    "streak_length": down_streak,
                    "atr_bps": round(atr_bps, 1),
                    "close": closes[-1]
                }
            )

        if up_streak >= self.min_streak:
            confidence = min(0.9, 0.55 + (up_streak - self.min_streak) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_reversal_up_exhaustion",
                    "streak_length": up_streak,
                    "atr_bps": round(atr_bps, 1),
                    "close": closes[-1]
                }
            )

        return None