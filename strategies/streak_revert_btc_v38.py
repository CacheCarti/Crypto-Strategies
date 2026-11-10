from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalStrategy(Strategy):
    METADATA = {
        "name": "StreakReversalBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # 4 hours intended holding
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5  # 5+ consecutive same-color bars
        self.cooldown_bars = 4     # cooldown after closing a position
        self.max_hold_bars = 6     # max bars to hold looking for reversal
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(20)
        opens = ctx.opens(20)
        if len(closes) < 15 or len(opens) < 15:
            return None

        # Position management: exit on opposite-color bar or max hold duration
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green = closes[-1] > opens[-1]
            is_red = closes[-1] < opens[-1]
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            if pos_dir == "long" and (is_green or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_streak_reversal_exit",
                    "bars_held": bars_held,
                    "is_green": is_green,
                    "close": closes[-1]
                })
            elif pos_dir == "short" and (is_red or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_streak_reversal_exit",
                    "bars_held": bars_held,
                    "is_red": is_red,
                    "close": closes[-1]
                })
            return None

        # Avoid entering during extreme crisis regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive same-color bars ending at current bar
        streak_down = 0
        streak_up = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c < o:
                if streak_up > 0:
                    break
                streak_down += 1
            elif c > o:
                if streak_down > 0:
                    break
                streak_up += 1
            else:
                break

        rsi = self._rsi(closes, 14)

        # Long exhaustion: 5+ consecutive down bars + RSI oversold confirmation
        if streak_down >= self.streak_threshold and rsi < 42.0:
            confidence = min(0.60 + (streak_down - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=250.0,
                take_profit_bps=350.0,
                metadata={
                    "reason": "bearish_streak_exhaustion_long",
                    "streak_down": streak_down,
                    "rsi": round(rsi, 2),
                    "close": closes[-1]
                }
            )

        # Short exhaustion: 5+ consecutive up bars + RSI overbought confirmation
        if streak_up >= self.streak_threshold and rsi > 58.0:
            confidence = min(0.60 + (streak_up - self.streak_threshold) * 0.1, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=250.0,
                take_profit_bps=350.0,
                metadata={
                    "reason": "bullish_streak_exhaustion_short",
                    "streak_up": streak_up,
                    "rsi": round(rsi, 2),
                    "close": closes[-1]
                }
            )

        return None