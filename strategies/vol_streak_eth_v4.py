from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "Volatility Streak Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for a few hours hold time
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV and regime
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5  # Number of consecutive bars in the same direction for a streak
        self.continuation_bars = 2 # Bars to check for failed continuation after streak
        self.atr_period = 20 # For volatility measure
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Store recent bar directions (up/down)
        self.recent_directions = collections.deque(maxlen=self.streak_period + self.continuation_bars)
        self.last_trade_direction = None

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Update recent directions
        if len(closes) > 1:
            current_direction = "up" if closes[-1] > closes[-2] else "down"
            self.recent_directions.append(current_direction)

        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None # Cooldown active

        if ctx.has_position():
            # If we are long and the market turns bearish, consider flattening
            if ctx.position_direction() == "long" and ctx.market.get("trend_regime") == "bear":
                return ctx.signal("flat", confidence=0.3, metadata={"reason": "trend_regime_flip_exit", "current_regime": ctx.market.get("trend_regime")})
            # If we are short and the market turns bullish, consider flattening
            if ctx.position_direction() == "short" and ctx.market.get("trend_regime") == "bull":
                return ctx.signal("flat", confidence=0.3, metadata={"reason": "trend_regime_flip_exit", "current_regime": ctx.market.get("trend_regime")})
            return ctx.signal("hold")

        # Regime filtering: Only trade in 'normal' or 'volatile' conditions, and not in strong trends
        current_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if current_regime == "CRISIS" or current_regime == "MELTDOWN" or trend_regime != "neutral":
            return None # Stand down in extreme crisis or strong trends

        # Calculate ATR for volatility context
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Check for streak and failed continuation
        if len(self.recent_directions) == self.streak_period + self.continuation_bars:
            streak_segment = list(self.recent_directions)[:self.streak_period]
            continuation_segment = list(self.recent_directions)[self.streak_period:]

            is_up_streak = all(d == "up" for d in streak_segment)
            is_down_streak = all(d == "down" for d in streak_segment)

            if is_up_streak and any(d == "down" for d in continuation_segment):
                # Up streak, but failed to continue up (at least one down bar)
                # Check if current bar is a down bar and previous was up
                if closes[-1] < closes[-2] and closes[-2] > closes[-3]:
                    # Also ensure current bar is within 1 ATR of the streak high
                    streak_high = max(highs[-(self.streak_period + self.continuation_bars):-self.continuation_bars])
                    if ctx.bar.close < streak_high + current_atr * 0.5: # Not too far from the high
                        self.last_signal_bar = ctx.bar_index
                        self.last_trade_direction = "short"
                        return ctx.signal("short", confidence=0.75,
                                          metadata={"reason": "failed_up_continuation",
                                                    "streak_direction": "up",
                                                    "current_close": ctx.bar.close,
                                                    "last_close": closes[-2],
                                                    "atr": current_atr,
                                                    "regime": current_regime})

            if is_down_streak and any(d == "up" for d in continuation_segment):
                # Down streak, but failed to continue down (at least one up bar)
                # Check if current bar is an up bar and previous was down
                if closes[-1] > closes[-2] and closes[-2] < closes[-3]:
                    # Also ensure current bar is within 1 ATR of the streak low
                    streak_low = min(lows[-(self.streak_period + self.continuation_bars):-self.continuation_bars])
                    if ctx.bar.close > streak_low - current_atr * 0.5: # Not too far from the low
                        self.last_signal_bar = ctx.bar_index
                        self.last_trade_direction = "long"
                        return ctx.signal("long", confidence=0.75,
                                          metadata={"reason": "failed_down_continuation",
                                                    "streak_direction": "down",
                                                    "current_close": ctx.bar.close,
                                                    "last_close": closes[-2],
                                                    "atr": current_atr,
                                                    "regime": current_regime})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on trade outcome
        pass