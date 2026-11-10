from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "StreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.max_hold_bars = 6

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green = closes[-1] > opens[-1]
            is_red = closes[-1] < opens[-1]

            # Snap exit: exit on the first opposite color bar
            if pos_dir == "long" and is_green:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "snap_reversal_green_exit", "close": closes[-1]}
                )
            elif pos_dir == "short" and is_red:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "snap_reversal_red_exit", "close": closes[-1]}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive directional bars
        up_streak = 0
        down_streak = 0

        # Check streak backwards from the most recent bar
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

        rsi = self._rsi(closes, period=14)
        atr = self._atr(highs, lows, closes, period=14)
        if rsi is None or atr is None:
            return None

        # Mean body size vs ATR to filter out zero-volatility drift
        streak_len = max(up_streak, down_streak)
        if streak_len < self.min_streak:
            return None

        recent_move = abs(closes[-1] - opens[-streak_len])
        if recent_move < (0.8 * atr):
            return None

        # Exhaustion setup
        if down_streak >= self.min_streak and rsi < 42.0:
            confidence = min(0.55 + (down_streak - self.min_streak) * 0.1, 0.90)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_snap",
                    "streak": down_streak,
                    "rsi": round(rsi, 2),
                    "move_ratio": round(recent_move / atr, 2),
                }
            )

        if up_streak >= self.min_streak and rsi > 58.0:
            confidence = min(0.55 + (up_streak - self.min_streak) * 0.1, 0.90)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_snap",
                    "streak": up_streak,
                    "rsi": round(rsi, 2),
                    "move_ratio": round(recent_move / atr, 2),
                }
            )

        return None