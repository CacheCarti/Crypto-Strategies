from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class StreakReversionExhaustion(Strategy):
    METADATA = {
        "name": "Streak Reversion Exhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 6
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.max_hold_bars = 5
        self.entry_bar = 0
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        opens = ctx.opens(45)
        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_bar_green = current_close > current_open
        current_bar_red = current_close < current_open

        # Position management: exit on opposite candle confirmation or time exhaustion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                if current_bar_green:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_streak_snapped_green_bar",
                            "bars_held": bars_held,
                            "close": current_close,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "long_time_stop_exhaustion",
                            "bars_held": bars_held,
                            "close": current_close,
                        },
                    )

            elif pos_dir == "short":
                if current_bar_red:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_streak_snapped_red_bar",
                            "bars_held": bars_held,
                            "close": current_close,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "short_time_stop_exhaustion",
                            "bars_held": bars_held,
                            "close": current_close,
                        },
                    )

            return None

        # Hard multi-bar cooldown guard after every exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter crisis & high volatility meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            return None

        # Count consecutive directional streaks
        down_streak = 0
        for i in range(1, min(20, len(closes))):
            if closes[-i] < opens[-i]:
                down_streak += 1
            else:
                break

        up_streak = 0
        for i in range(1, min(20, len(closes))):
            if closes[-i] > opens[-i]:
                up_streak += 1
            else:
                break

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Long Entry: 6+ consecutive down bars + strict oversold condition (RSI <= 34)
        if down_streak >= self.streak_threshold and rsi <= 34.0:
            confidence = min(0.65 + (down_streak - self.streak_threshold) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "down_streak_exhaustion_oversold",
                    "down_streak": down_streak,
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "price": current_close,
                },
            )

        # Short Entry: 6+ consecutive up bars + strict overbought condition (RSI >= 66)
        if up_streak >= self.streak_threshold and rsi >= 66.0:
            confidence = min(0.65 + (up_streak - self.streak_threshold) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "up_streak_exhaustion_overbought",
                    "up_streak": up_streak,
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "price": current_close,
                },
            )

        return None