from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SelectiveStreakReversion(Strategy):
    METADATA = {
        "name": "BTC Exhaustion Streak Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        opens = ctx.opens(40)
        if len(closes) < 30 or len(opens) < 30:
            return None

        # Check existing position for exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            is_green = ctx.bar.close > ctx.bar.open
            is_red = ctx.bar.close < ctx.bar.open

            # Snap exit: first opposite color bar or maximum time hold
            if pos_dir == "long" and (is_green or bars_held >= self.max_hold_bars):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "streak_snapped_long" if is_green else "time_stop_long", "bars_held": bars_held}
                )
            elif pos_dir == "short" and (is_red or bars_held >= self.max_hold_bars):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "streak_snapped_short" if is_red else "time_stop_short", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid entering during absolute crisis volatility spikes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Count uninterrupted consecutive candle color streak
        streak_len = 0
        streak_type = None  # "up" or "down"
        for i in range(len(closes) - 1, -1, -1):
            c = closes[i]
            o = opens[i]
            if c > o:
                current_type = "up"
            elif c < o:
                current_type = "down"
            else:
                break

            if streak_type is None:
                streak_type = current_type
                streak_len = 1
            elif current_type == streak_type:
                streak_len += 1
            else:
                break

        # Only trigger at the threshold transition (exactly min_streak or higher after sustained push)
        if streak_len < self.min_streak:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Long exhaustion setup: 6+ consecutive down bars accompanied by oversold momentum
        if streak_type == "down" and rsi < 36.0:
            confidence = min(0.60 + (streak_len - self.min_streak) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_len": streak_len,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        # Short exhaustion setup: 6+ consecutive up bars accompanied by overbought momentum
        if streak_type == "up" and rsi > 64.0:
            confidence = min(0.60 + (streak_len - self.min_streak) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_len": streak_len,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        return None