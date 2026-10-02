from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BTC Streak Exhaustion Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 10800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 10
        self.max_hold_bars = 8
        self.rsi_period = 14
        self.rsi_long_threshold = 34.0
        self.rsi_short_threshold = 66.0
        self.last_exit_bar = -100
        self.entry_bar_index = -1

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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

    def _calc_streak(self, opens, closes):
        up_streak = 0
        down_streak = 0
        n = len(closes)
        for i in range(n - 1, -1, -1):
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
        return up_streak, down_streak

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_index = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        rsi = self._rsi(closes, self.rsi_period) or 50.0

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar_index if self.entry_bar_index > 0 else 1

            if direction == "long":
                # Exit long on confirmation of green snap bar or timeout
                if current_close > current_open and bars_held >= 1:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_streak_snap_green_exit",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": current_close,
                            "open": current_open
                        }
                    )
                elif bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "long_max_hold_timeout",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": current_close
                        }
                    )

            elif direction == "short":
                # Exit short on confirmation of red snap bar or timeout
                if current_close < current_open and bars_held >= 1:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_streak_snap_red_exit",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": current_close,
                            "open": current_open
                        }
                    )
                elif bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.50,
                        metadata={
                            "reason": "short_max_hold_timeout",
                            "bars_held": bars_held,
                            "rsi": rsi,
                            "close": current_close
                        }
                    )
            return None

        # Hard Cooldown to throttle trade count and eliminate friction burn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out volatile meltdown/crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        up_streak, down_streak = self._calc_streak(opens, closes)

        # High-conviction Long: 6+ consecutive down bars + RSI deeply oversold
        if down_streak >= self.min_streak and rsi <= self.rsi_long_threshold:
            confidence = min(0.65 + (down_streak - self.min_streak) * 0.08, 0.95)
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_consecutive_down_streak_exhaustion",
                    "streak_count": down_streak,
                    "rsi": rsi,
                    "close": current_close,
                    "open": current_open
                }
            )

        # High-conviction Short: 6+ consecutive up bars + RSI deeply overbought
        if up_streak >= self.min_streak and rsi >= self.rsi_short_threshold:
            confidence = min(0.65 + (up_streak - self.min_streak) * 0.08, 0.95)
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_consecutive_up_streak_exhaustion",
                    "streak_count": up_streak,
                    "rsi": rsi,
                    "close": current_close,
                    "open": current_open
                }
            )

        return None