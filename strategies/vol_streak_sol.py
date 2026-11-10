from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolStreakFade(Strategy):
    METADATA = {
        "name": "SOL Volatility Streak Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 10
        self.continuation_bars = 3
        self.cooldown_bars = 20 # Wait 20 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.streak_period + self.continuation_bars + 1)
        highs = ctx.highs(self.streak_period + self.continuation_bars + 1)
        lows = ctx.lows(self.streak_period + self.continuation_bars + 1)
        volumes = ctx.volumes(self.streak_period + self.continuation_bars + 1)

        if len(closes) < self.streak_period + self.continuation_bars + 1:
            return None

        if ctx.has_position():
            return None # Already in a position, wait for it to close

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None # Cooldown period

        # Calculate volatility/directional streak
        # Look for a strong directional move over streak_period
        streak_closes = closes[-(self.streak_period + self.continuation_bars):-self.continuation_bars]
        if len(streak_closes) < self.streak_period:
            return None

        # Check for a significant move
        first_close_of_streak = streak_closes[0]
        last_close_of_streak = streak_closes[-1]
        streak_change_pct = (last_close_of_streak - first_close_of_streak) / first_close_of_streak

        # ATR for volatility context
        current_atr = self._atr(highs, lows, closes, period=self.streak_period)
        if current_atr is None:
            return None
        
        # Define a significant move threshold (e.g., 2x ATR change over the streak)
        # We use a simple percentage for now, more advanced would be ATR based
        min_streak_pct = 0.015 # 1.5% move over streak_period (e.g. 10 hours)
        
        is_bull_streak = streak_change_pct > min_streak_pct
        is_bear_streak = streak_change_pct < -min_streak_pct

        # Check for failed continuation after the streak
        continuation_closes = closes[-self.continuation_bars:]
        if len(continuation_closes) < self.continuation_bars:
            return None

        current_close = ctx.bar.close

        signal_direction = None
        reason = ""
        
        # Long opportunity: Bear streak followed by failed bearish continuation
        if is_bear_streak:
            # Check if continuation bars failed to continue downwards
            # A "failed continuation" means prices are not making lower lows/closes or are reversing
            # Simple check: current close is above the close of the first continuation bar
            # Or, current close has recovered significantly from the streak low
            
            # The close immediately after the streak (start of continuation period)
            close_after_streak = closes[-self.continuation_bars - 1] 
            
            # If the current close is higher than the close right after the bear streak,
            # and the market regime isn't extremely bearish
            if current_close > close_after_streak and ctx.market.get("trend_regime", "neutral") != "bear":
                 # Ensure we are not still in an active downtrend within the continuation bars
                # i.e., current close is not the lowest in the continuation period
                if current_close > min(continuation_closes):
                    signal_direction = "long"
                    reason = "bear_streak_failed_continuation_long"

        # Short opportunity: Bull streak followed by failed bullish continuation
        elif is_bull_streak:
            # Check if continuation bars failed to continue upwards
            # Simple check: current close is below the close of the first continuation bar
            # Or, current close has fallen significantly from the streak high
            
            # The close immediately after the streak (start of continuation period)
            close_after_streak = closes[-self.continuation_bars - 1]
            
            # If the current close is lower than the close right after the bull streak,
            # and the market regime isn't extremely bullish
            if current_close < close_after_streak and ctx.market.get("trend_regime", "neutral") != "bull":
                # Ensure we are not still in an active uptrend within the continuation bars
                # i.e., current close is not the highest in the continuation period
                if current_close < max(continuation_closes):
                    signal_direction = "short"
                    reason = "bull_streak_failed_continuation_short"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            # Confidence based on how strong the initial streak was and current regime
            confidence = min(0.5 + abs(streak_change_pct) * 10, 0.9) # Scale confidence with streak strength
            
            # Add some additional filtering based on market regime
            if ctx.market.get("regime") == "CRISIS":
                confidence *= 0.7 # Reduce confidence in crisis
            elif ctx.market.get("regime") == "HIGH_VOL":
                confidence *= 0.8 # Reduce confidence in high vol
                
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_close,
                    "streak_period": self.streak_period,
                    "continuation_bars": self.continuation_bars,
                    "streak_change_pct": f"{streak_change_pct:.2%}",
                    "current_atr": f"{current_atr:.2f}",
                    "market_regime": ctx.market.get("regime"),
                    "trend_regime": ctx.market.get("trend_regime"),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly necessary for this strategy, but good practice to include
        # Could be used to adapt parameters based on recent performance
        pass