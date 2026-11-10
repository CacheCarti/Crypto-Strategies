from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "Volatility Streak Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 10
        self.volatility_period = 20
        self.cooldown_bars = 10
        self.last_signal_bar_index = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        # Calculate average ATR over a longer period to normalize
        avg_atr_long = self._atr(highs, lows, closes, self.warmup_bars - 1)
        if avg_atr_long is None or avg_atr_long == 0:
            return None

        atr_ratio = current_atr / avg_atr_long

        # Detect extreme same-direction streak
        # A simple way to detect a streak is to count consecutive bars closing higher/lower
        # and measure the associated volatility.
        # We look for a series of bars where the close is significantly higher/lower than the open,
        # indicating strong directional movement, AND high volatility.
        
        # Check for bullish streak followed by a failed continuation
        bull_streak_count = 0
        streak_volatility = 0.0
        for i in range(1, self.streak_period + 1):
            if closes[-i] > opens[-i]:
                bull_streak_count += 1
                streak_volatility += (highs[-i] - lows[-i])
            else:
                break
        
        # Check for bearish streak followed by a failed continuation
        bear_streak_count = 0
        for i in range(1, self.streak_period + 1):
            if closes[-i] < opens[-i]:
                bear_streak_count += 1
                streak_volatility += (highs[-i] - lows[-i])
            else:
                break

        # Condition: high volatility + strong streak, then a "failed continuation"
        # A failed continuation is when after a strong streak, the current bar reverses direction
        # or shows weakness relative to the streak.

        # Long entry: Fade a strong bearish streak if current bar shows reversal signs
        if bear_streak_count >= self.streak_period * 0.7 and \
           atr_ratio > 1.2 and \
           ctx.bar.close > ctx.bar.open and \
           (ctx.bar.close - ctx.bar.open) / ctx.bar.open > 0.001: # Current bar is bullish reversal
            
            if not ctx.has_position():
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fade_bear_streak_reversal",
                        "bear_streak_count": bear_streak_count,
                        "current_atr_ratio": atr_ratio,
                        "current_bar_change_pct": (ctx.bar.close - ctx.bar.open) / ctx.bar.open * 100,
                        "close": ctx.bar.close,
                    }
                )

        # Short entry: Fade a strong bullish streak if current bar shows reversal signs
        if bull_streak_count >= self.streak_period * 0.7 and \
           atr_ratio > 1.2 and \
           ctx.bar.close < ctx.bar.open and \
           (ctx.bar.open - ctx.bar.close) / ctx.bar.open > 0.001: # Current bar is bearish reversal
            
            if not ctx.has_position():
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fade_bull_streak_reversal",
                        "bull_streak_count": bull_streak_count,
                        "current_atr_ratio": atr_ratio,
                        "current_bar_change_pct": (ctx.bar.open - ctx.bar.close) / ctx.bar.open * 100,
                        "close": ctx.bar.close,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this exploration, just reset cooldown (handled by setting last_signal_bar_index)
        pass