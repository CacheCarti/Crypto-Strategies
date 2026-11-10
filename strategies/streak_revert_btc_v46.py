from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalAdaptive(Strategy):
    METADATA = {
        "name": "StreakReversalAdaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.max_hold_bars = 5
        self.entry_bar = -100
        self.lookback = 48

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

    def _count_streak(self, closes, opens):
        n = len(closes)
        if n < 2:
            return 0
        last_sign = 1 if closes[-1] > opens[-1] else (-1 if closes[-1] < opens[-1] else 0)
        if last_sign == 0:
            return 0
        streak = 0
        for i in range(n - 1, -1, -1):
            sign = 1 if closes[i] > opens[i] else (-1 if closes[i] < opens[i] else 0)
            if sign == last_sign:
                streak += 1
            else:
                break
        return streak * last_sign

    def _historical_max_streak(self, closes, opens, window=48):
        max_s = 3
        curr_streak = 0
        curr_sign = 0
        start = max(0, len(closes) - window)
        for i in range(start, len(closes) - 1):
            sign = 1 if closes[i] > opens[i] else (-1 if closes[i] < opens[i] else 0)
            if sign == 0:
                curr_streak = 0
                curr_sign = 0
                continue
            if sign == curr_sign:
                curr_streak += 1
            else:
                curr_sign = sign
                curr_streak = 1
            if curr_streak > max_s:
                max_s = curr_streak
        return max_s

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        opens = ctx.opens(self.lookback + 2)
        if len(closes) < self.lookback:
            return None

        current_idx = ctx.bar_index
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic
        if has_pos:
            bars_held = current_idx - self.entry_bar
            is_green = ctx.bar.close > ctx.bar.open
            is_red = ctx.bar.close < ctx.bar.open

            should_exit = False
            reason = "hold"

            if pos_dir == "long" and is_green and bars_held >= 1:
                should_exit = True
                reason = "streak_snapped_green"
            elif pos_dir == "short" and is_red and bars_held >= 1:
                should_exit = True
                reason = "streak_snapped_red"
            elif bars_held >= self.max_hold_bars:
                should_exit = True
                reason = "max_time_stop"

            if should_exit:
                self.last_exit_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Streak calculation
        streak = self._count_streak(closes, opens)
        abs_streak = abs(streak)
        rolling_max = self._historical_max_streak(closes, opens, window=self.lookback)
        threshold = max(4, min(rolling_max, 6))

        if abs_streak < threshold:
            return None

        rsi = self._rsi(closes, period=14)
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Scale confidence based on streak extremity
        conf = min(0.85, 0.55 + 0.08 * (abs_streak - threshold))

        # Long exhaustion: series of down bars + oversold confirmation + non-overcrowded longs
        if streak <= -threshold and rsi < 42 and funding < 0.0003:
            self.entry_bar = current_idx
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "streak_reversal_exhaustion_down",
                    "streak": streak,
                    "threshold": threshold,
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "close": ctx.bar.close,
                },
            )

        # Short exhaustion: series of up bars + overbought confirmation + non-discounted funding
        if streak >= threshold and rsi > 58 and funding > -0.0003:
            self.entry_bar = current_idx
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "streak_reversal_exhaustion_up",
                    "streak": streak,
                    "threshold": threshold,
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "close": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index