from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedContinuationFade(Strategy):
    METADATA = {
        "name": "Failed Continuation Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # Aim for a couple of hours hold
        "warmup_bars": 50, # Need enough bars for streak detection and ATR
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5 # Number of consecutive same-direction bars to define a streak
        self.atr_period = 14
        self.cooldown_bars = 10 # Wait this many bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.streak_period + 2) # Need current bar + streak + 1 for failed continuation
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.streak_period + 2 or len(highs) < self.atr_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]
        two_prev_close = closes[-3]

        # Calculate ATR for stop-loss and take-profit sizing
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Check for failed continuation after a strong streak
        # A strong streak is N consecutive bars moving in the same direction (e.g., all closes higher than previous close)
        # A failed continuation is when after such a streak, the very next bar reverses direction significantly.

        # Long entry: Fade a failed bearish continuation after a strong bearish streak
        # Strong bearish streak: N consecutive bars where close < previous close
        # Failed continuation: The next bar (current_bar) closes significantly higher, breaking the streak.
        is_bearish_streak = True
        for i in range(1, self.streak_period + 1):
            if closes[-(i+1)] >= closes[-i]: # Check if close[t-i] is NOT less than close[t-i-1]
                is_bearish_streak = False
                break

        if is_bearish_streak:
            # Streak ended on the bar before current bar (closes[-2] vs closes[-3])
            # Failed continuation if current bar is strongly bullish
            if current_close > closes[-2] and (current_close - closes[-2]) > (current_atr * 0.5): # current bar is bullish and significant
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "fade_failed_bearish_continuation",
                            "streak_length": self.streak_period,
                            "current_close": current_close,
                            "prev_close": closes[-2],
                            "atr": current_atr,
                        }
                    )

        # Short entry: Fade a failed bullish continuation after a strong bullish streak
        # Strong bullish streak: N consecutive bars where close > previous close
        # Failed continuation: The next bar (current_bar) closes significantly lower, breaking the streak.
        is_bullish_streak = True
        for i in range(1, self.streak_period + 1):
            if closes[-(i+1)] <= closes[-i]: # Check if close[t-i] is NOT greater than close[t-i-1]
                is_bullish_streak = False
                break

        if is_bullish_streak:
            # Streak ended on the bar before current bar (closes[-2] vs closes[-3])
            # Failed continuation if current bar is strongly bearish
            if current_close < closes[-2] and (closes[-2] - current_close) > (current_atr * 0.5): # current bar is bearish and significant
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "fade_failed_bullish_continuation",
                            "streak_length": self.streak_period,
                            "current_close": current_close,
                            "prev_close": closes[-2],
                            "atr": current_atr,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last_trade_bar to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass