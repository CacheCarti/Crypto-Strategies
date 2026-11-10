from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_position = 0

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
        n_bars = 30
        closes = ctx.closes(n_bars)
        opens = ctx.opens(n_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing open position
        if ctx.has_position():
            self.bars_in_position += 1
            pos_dir = ctx.position_direction()
            curr_close = closes[-1]
            curr_open = opens[-1]

            # Exit rule: snapped candle (opposite color) or short time-stop (max 4 bars)
            snapped = (pos_dir == "long" and curr_close > curr_open) or (pos_dir == "short" and curr_close < curr_open)
            if snapped or self.bars_in_position >= 4:
                reason = "streak_snap_confirmed" if snapped else "time_limit_exhaustion"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": reason, "bars_held": self.bars_in_position, "close": curr_close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis regime guard (avoid catching falling knives in liquidation cascades)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Count consecutive directional bars
        up_streak = 0
        down_streak = 0
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
        if rsi is None:
            return None

        # Redesigned entry boundary: strict exhaustion boundary with RSI stretch confirmation
        # 6+ consecutive down bars + RSI stretched oversold (< 36) -> snap Long
        if down_streak >= self.min_streak and rsi < 36.0:
            conf = min(0.55 + (down_streak - self.min_streak) * 0.1, 0.95)
            self.bars_in_position = 0
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_long",
                    "streak_length": down_streak,
                    "rsi": round(rsi, 2),
                    "close": closes[-1]
                }
            )

        # 6+ consecutive up bars + RSI stretched overbought (> 64) -> snap Short
        if up_streak >= self.min_streak and rsi > 64.0:
            conf = min(0.55 + (up_streak - self.min_streak) * 0.1, 0.95)
            self.bars_in_position = 0
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_short",
                    "streak_length": up_streak,
                    "rsi": round(rsi, 2),
                    "close": closes[-1]
                }
            )

        return None