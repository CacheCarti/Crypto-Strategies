from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalVolatility(Strategy):
    METADATA = {
        "name": "StreakReversalVolatility",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_position = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Manage open position
        if ctx.has_position():
            self.bars_in_position += 1
            pos_dir = ctx.position_direction()
            
            # Snap exit: first bar closing opposite to trade direction
            if pos_dir == "long" and current_close > current_open:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_green_bar_snap", "bars_held": self.bars_in_position}
                )
            elif pos_dir == "short" and current_close < current_open:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_red_bar_snap", "bars_held": self.bars_in_position}
                )

            # Time-based hold guard
            if self.bars_in_position >= 5:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": self.bars_in_position}
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count consecutive directional bars
        down_streak = 0
        up_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c < o:
                if up_streak > 0:
                    break
                down_streak += 1
            elif c > o:
                if down_streak > 0:
                    break
                up_streak += 1
            else:
                break

        if down_streak < self.streak_threshold and up_streak < self.streak_threshold:
            return None

        # Volatility & Momentum confirmation to avoid failed drift
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        
        atr_ratio = (ctx.bar.high - ctx.bar.low) / atr
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Long Exhaustion Reversal
        if down_streak >= self.streak_threshold:
            # Requires expansion bar at the climax or severe RSI exhaustion
            if (atr_ratio > 0.85 or rsi < 36.0) and rsi < 45.0:
                conf = min(0.9, 0.55 + 0.08 * (down_streak - self.streak_threshold) + 0.1 * min(atr_ratio, 1.5))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "exhaustion_down_streak_snap",
                        "down_streak": down_streak,
                        "atr_ratio": round(atr_ratio, 2),
                        "rsi": round(rsi, 2)
                    }
                )

        # Short Exhaustion Reversal
        if up_streak >= self.streak_threshold:
            if (atr_ratio > 0.85 or rsi > 64.0) and rsi > 55.0:
                conf = min(0.9, 0.55 + 0.08 * (up_streak - self.streak_threshold) + 0.1 * min(atr_ratio, 1.5))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "exhaustion_up_streak_snap",
                        "up_streak": up_streak,
                        "atr_ratio": round(atr_ratio, 2),
                        "rsi": round(rsi, 2)
                    }
                )

        return None