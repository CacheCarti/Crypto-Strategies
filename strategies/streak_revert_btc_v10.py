from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalBtc(Strategy):
    METADATA = {
        "name": "StreakReversalBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", closes[-1]) if ctx.position else closes[-1]
            bars_held = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 1

            # Snap exit: first opposite-color bar or time exhaustion
            is_green_bar = closes[-1] > opens[-1]
            is_red_bar = closes[-1] < opens[-1]

            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if is_green_bar and closes[-1] > entry_price:
                    should_exit = True
                    exit_reason = "streak_snap_green_take_profit"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "max_hold_exhaustion"
            elif pos_dir == "short":
                if is_red_bar and closes[-1] < entry_price:
                    should_exit = True
                    exit_reason = "streak_snap_red_take_profit"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "max_hold_exhaustion"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": exit_reason, "price": closes[-1], "entry_price": entry_price}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count consecutive directional bars
        down_streak = 0
        up_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
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

        # Regime and RSI confirmation to prevent fading runaway trends
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        atr_ratio = (atr / closes[-1]) * 10000.0 if closes[-1] > 0 else 0.0

        # Avoid extreme volatility/meltdown regimes where trends do not mean-revert cleanly
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long exhaustion: 5+ down bars + RSI oversold confirmation
        if down_streak >= self.min_streak and rsi < 36.0:
            confidence = min(0.55 + (down_streak - self.min_streak) * 0.1, 0.95)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_count": down_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_ratio, 1),
                    "price": closes[-1]
                }
            )

        # Short exhaustion: 5+ up bars + RSI overbought confirmation
        if up_streak >= self.min_streak and rsi > 64.0:
            confidence = min(0.55 + (up_streak - self.min_streak) * 0.1, 0.95)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_count": up_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_ratio, 1),
                    "price": closes[-1]
                }
            )

        return None