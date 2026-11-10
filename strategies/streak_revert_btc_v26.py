from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DynamicStreakExhaustion(Strategy):
    METADATA = {
        "name": "Dynamic Streak Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_pos = 0
        self.max_hold_bars = 6

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        if len(closes) < 50 or len(opens) < 50:
            return None

        # Track position duration
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()
            current_close = ctx.bar.close
            current_open = ctx.bar.open

            # Exit on first opposite-color bar snapped or time stop reached
            opposite_bar = (direction == "long" and current_close < current_open) or \
                           (direction == "short" and current_close > current_open)

            if opposite_bar or self.bars_in_pos >= self.max_hold_bars:
                reason = "streak_snap_opposite_bar" if opposite_bar else "time_stop_exhaustion"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": reason,
                        "bars_in_pos": self.bars_in_pos,
                        "price": current_close
                    }
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count consecutive directional bars
        up_streak = 0
        down_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c > o:
                if down_streak > 0:
                    break
                up_streak += 1
            elif c < o:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        # Avoid crisis/meltdown regimes for counter-trend fading
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic streak threshold based on recent return volatility
        # Using 5 or 6 depending on RSI alignment
        rsi = self._rsi(closes, period=14)

        # Long exhaustion: extended downward streak + oversold confirmation
        if down_streak >= 5 and rsi < 36.0:
            conf = min(0.55 + (down_streak - 5) * 0.1, 0.90)
            self.bars_in_pos = 0
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "streak_reversal_exhaustion_long",
                    "down_streak": down_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        # Short exhaustion: extended upward streak + overbought confirmation
        if up_streak >= 5 and rsi > 64.0:
            conf = min(0.55 + (up_streak - 5) * 0.1, 0.90)
            self.bars_in_pos = 0
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "streak_reversal_exhaustion_short",
                    "up_streak": up_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        return None