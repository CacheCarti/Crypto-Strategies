from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedContinuationFade(Strategy):
    METADATA = {
        "name": "Failed Continuation Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # 3 hours average hold
        "warmup_bars": 60,  # Need enough history for EMA and streak detection
        "required_features": [], # No external features needed
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.streak_length = 3  # Number of consecutive bars in same direction
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Calculate EMA for trend context
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Detect streaks
        long_streak = 0
        short_streak = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > opens[i]: # Green candle
                long_streak += 1
                short_streak = 0
            elif closes[i] < opens[i]: # Red candle
                short_streak += 1
                long_streak = 0
            else: # Doji or flat, reset
                long_streak = 0
                short_streak = 0

            # Stop counting if streak is broken or we've gone back far enough
            if long_streak == 0 and short_streak == 0:
                break

        # Entry logic: Fade the first failed continuation after an extreme same-direction streak
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long entry: Fade a failed bearish continuation
        # Condition 1: Previous bars showed a strong bearish streak
        # Condition 2: Current bar attempts to continue the bearish trend but fails (e.g., closes above its open, or forms a hammer/doji)
        # Condition 3: Price is generally above a short-term EMA (slight bullish bias for the fade)
        if short_streak >= self.streak_length:
            # Check for failed bearish continuation: current bar is green (closes > open)
            # or current bar is a strong reversal candle (e.g., hammer-like, closes near high)
            if current_close > current_open and current_close > closes[-2]: # Current bar is green and higher than previous close
                if current_close > ema_val: # Confirm with EMA for slight bullish bias
                    signal_direction = "long"
                    reason = f"failed_bear_continuation_fade_long_after_{short_streak}_red_bars_above_ema"
                    confidence = min(0.7, 0.5 + (short_streak - self.streak_length) * 0.1) # Confidence scales with streak length

        # Short entry: Fade a failed bullish continuation
        # Condition 1: Previous bars showed a strong bullish streak
        # Condition 2: Current bar attempts to continue the bullish trend but fails (e.g., closes below its open, or forms a shooting star/doji)
        # Condition 3: Price is generally below a short-term EMA (slight bearish bias for the fade)
        elif long_streak >= self.streak_length:
            # Check for failed bullish continuation: current bar is red (closes < open)
            # or current bar is a strong reversal candle (e.g., shooting star-like, closes near low)
            if current_close < current_open and current_close < closes[-2]: # Current bar is red and lower than previous close
                if current_close < ema_val: # Confirm with EMA for slight bearish bias
                    signal_direction = "short"
                    reason = f"failed_bull_continuation_fade_short_after_{long_streak}_green_bars_below_ema"
                    confidence = min(0.7, 0.5 + (long_streak - self.streak_length) * 0.1) # Confidence scales with streak length

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_close": current_close,
                    "current_open": current_open,
                    "ema_val": ema_val,
                    "short_streak": short_streak,
                    "long_streak": long_streak,
                }
            )

        # If we have a position, consider exiting if the fade logic reverses or becomes weak
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # If current bar is a strong red candle and below EMA, consider exiting
                if current_close < current_open and current_close < ema_val and current_open - current_close > (highs[-1] - lows[-1]) * 0.5:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_long_bearish_reversal_below_ema", "current_close": current_close, "ema_val": ema_val})
            elif ctx.position_direction() == "short":
                # If current bar is a strong green candle and above EMA, consider exiting
                if current_close > current_open and current_close > ema_val and current_close - current_open > (highs[-1] - lows[-1]) * 0.5:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_short_bullish_reversal_above_ema", "current_close": current_close, "ema_val": ema_val})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass