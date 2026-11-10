from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "Streak Reversal BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.min_streak = 6

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

        if len(closes) < 20 or len(opens) < 20:
            return None

        # Manage open position exit: exit on first opposite-color bar
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_is_green = ctx.bar.close > ctx.bar.open
            curr_is_red = ctx.bar.close < ctx.bar.open

            if pos_dir == "long" and curr_is_green:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "streak_snapped_green_exit", "close": ctx.bar.close})
            elif pos_dir == "short" and curr_is_red:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "streak_snapped_red_exit", "close": ctx.bar.close})
            return None

        # Enforce cooldown after prior trade
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown conditions
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive same-color bars up to current bar
        streak_len = 0
        streak_type = None

        for i in range(len(closes) - 1, -1, -1):
            is_green = closes[i] > opens[i]
            is_red = closes[i] < opens[i]

            if not is_green and not is_red:
                break

            current_type = "green" if is_green else "red"
            if streak_type is None:
                streak_type = current_type
                streak_len = 1
            elif current_type == streak_type:
                streak_len += 1
            else:
                break

        if streak_len < self.min_streak:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Long exhaustion setup: 6+ down bars confirmed by oversold momentum
        if streak_type == "red" and rsi < 36.0:
            confidence = min(0.60 + 0.08 * (streak_len - self.min_streak), 0.95)
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

        # Short exhaustion setup: 6+ up bars confirmed by overbought momentum
        if streak_type == "green" and rsi > 64.0:
            confidence = min(0.60 + 0.08 * (streak_len - self.min_streak), 0.95)
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