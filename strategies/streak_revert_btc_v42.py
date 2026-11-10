from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SelectiveStreakReversion(Strategy):
    METADATA = {
        "name": "BTC Exhaustion Streak Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 14400,  # ~4 hours target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < 25 or len(opens) < 25:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        bar_idx = ctx.bar_index

        # Position management: Exit on first snap (opposite color) or time stop (6 bars)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = bar_idx - self.entry_bar
            is_green = current_close > current_open
            is_red = current_close < current_open

            if pos_dir == "long" and (is_green or bars_held >= 6):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_streak_snap_or_timeout", "bars_held": bars_held, "is_green": is_green}
                )
            elif pos_dir == "short" and (is_red or bars_held >= 6):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_streak_snap_or_timeout", "bars_held": bars_held, "is_red": is_red}
                )
            return None

        # Mandatory cooldown guard
        if bar_idx - self.last_exit_bar < self.cooldown_period:
            return None

        # Count consecutive directional bars leading up to current bar
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

        # Regime and momentum confirmation to reject blind counter-trend punts
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # 5+ consecutive down bars + RSI oversold (<35) -> Exhaustion Long
        if down_streak >= self.min_streak and rsi < 35.0:
            confidence = min(0.60 + 0.08 * (down_streak - self.min_streak), 0.95)
            self.entry_bar = bar_idx
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=320.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bear_streak_exhaustion_snap",
                    "streak_len": down_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                }
            )

        # 5+ consecutive up bars + RSI overbought (>65) -> Exhaustion Short
        if up_streak >= self.min_streak and rsi > 65.0:
            confidence = min(0.60 + 0.08 * (up_streak - self.min_streak), 0.95)
            self.entry_bar = bar_idx
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=320.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bull_streak_exhaustion_snap",
                    "streak_len": up_streak,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                }
            )

        return None