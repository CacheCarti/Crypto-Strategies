from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class StreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "StreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 4
        self.cooldown_bars = 4
        self.max_hold_bars = 8
        self.rsi_period = 14
        self.entry_bar = -100
        self.last_exit_bar = -100

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

    def _get_streaks(self, opens, closes):
        down_streak = 0
        up_streak = 0
        for o, c in zip(reversed(opens), reversed(closes)):
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
        return down_streak, up_streak

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Position management and profit/loss exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            current_bar_green = ctx.bar.close > ctx.bar.open
            current_bar_red = ctx.bar.close < ctx.bar.open

            # Exit on counter-candle snap
            if pos_dir == "long" and current_bar_green and bars_held >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "streak_snapped_green_close",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and current_bar_red and bars_held >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "streak_snapped_red_close",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )

            # Max hold time exit
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.60,
                    metadata={
                        "reason": "max_bars_exhaustion_timeout",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )

            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        down_streak, up_streak = self._get_streaks(opens, closes)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Long Entry: Down streak exhaustion
        if down_streak >= self.min_streak and rsi < 50.0:
            confidence = min(0.95, 0.60 + (down_streak - self.min_streak) * 0.10)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "consecutive_down_bars_exhaustion_long",
                    "streak_len": down_streak,
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "price": ctx.bar.close,
                },
            )

        # Short Entry: Up streak exhaustion
        if up_streak >= self.min_streak and rsi > 50.0:
            confidence = min(0.95, 0.60 + (up_streak - self.min_streak) * 0.10)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "consecutive_up_bars_exhaustion_short",
                    "streak_len": up_streak,
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "price": ctx.bar.close,
                },
            )

        return None