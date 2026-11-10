from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "VolatilityStreakFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5 # Number of consecutive bars in the same direction
        self.continuation_fail_bars = 2 # How many bars must fail to continue
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Parameters for regime filtering
        self.volatility_period = 20
        self.trend_period = 50

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime Filtering
        # Avoid trading during extreme volatility (high ATR relative to recent ATR)
        atr_current = self._atr(highs, lows, closes, self.volatility_period)
        if atr_current is None:
            return None
        
        # Simple volatility check: if current ATR is too high, stand down
        # We need a longer history for a meaningful comparison
        if len(closes) > self.volatility_period * 2:
            recent_atrs = []
            for i in range(self.volatility_period, len(closes)):
                recent_atrs.append(self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.volatility_period))
            
            # Filter out None values from recent_atrs
            recent_atrs = [atr for atr in recent_atrs if atr is not None]

            if len(recent_atrs) > 5: # Need a few recent ATRs for comparison
                avg_recent_atr = sum(recent_atrs[-5:]) / len(recent_atrs[-5:])
                if atr_current > avg_recent_atr * 1.5: # If current volatility is 50% higher than recent average
                    return None # Stand down in very high volatility

        # Trend filter: only fade streaks against the prevailing trend to increase conviction
        sma_long = self._sma(closes, self.trend_period)
        if sma_long is None:
            return None
        
        is_uptrend = ctx.bar.close > sma_long
        is_downtrend = ctx.bar.close < sma_long

        # Streak detection
        current_streak_direction = 0 # 1 for up, -1 for down, 0 for mixed
        current_streak_length = 0

        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]: # Up bar
                if current_streak_direction == 1:
                    current_streak_length += 1
                else:
                    current_streak_direction = 1
                    current_streak_length = 1
            elif closes[i] < closes[i-1]: # Down bar
                if current_streak_direction == -1:
                    current_streak_length += 1
                else:
                    current_streak_direction = -1
                    current_streak_length = 1
            else: # Doji or flat bar, resets streak
                current_streak_direction = 0
                current_streak_length = 0

        # Check for failed continuation after an extreme streak
        # We need to look back from the current bar's perspective
        # A streak ends at bar N-1, and bar N fails to continue it.

        # Look for a streak ending 'continuation_fail_bars' bars ago
        # And check if the subsequent bars failed to continue that streak
        
        # Check for long entry (fade a down streak)
        if not ctx.has_position():
            # Find the end of a potential down streak
            down_streak_end_index = -1
            temp_streak_len = 0
            temp_streak_dir = 0
            for i in range(len(closes) - 2, -1, -1): # Iterate backwards from second to last bar
                if closes[i+1] < closes[i]: # Down bar
                    if temp_streak_dir == -1:
                        temp_streak_len += 1
                    else:
                        temp_streak_dir = -1
                        temp_streak_len = 1
                else:
                    break # Streak broken
                
                if temp_streak_dir == -1 and temp_streak_len >= self.streak_period:
                    down_streak_end_index = i + 1 # The bar where the streak ended
                    break
            
            if down_streak_end_index != -1 and down_streak_end_index + self.continuation_fail_bars < len(closes):
                # Streak ended at down_streak_end_index. Now check bars after it.
                # All bars from down_streak_end_index+1 up to current must NOT be down bars
                failed_continuation = True
                for i in range(down_streak_end_index + 1, len(closes)):
                    if closes[i] < closes[i-1]: # If any bar continued the down trend
                        failed_continuation = False
                        break
                
                if failed_continuation and is_uptrend: # Only fade down streak if overall trend is up
                    # The current bar is the one confirming the failed continuation
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "down_streak_fade_failed_continuation",
                            "streak_length": temp_streak_len,
                            "fail_bars": self.continuation_fail_bars,
                            "current_close": ctx.bar.close,
                            "sma_long": sma_long
                        }
                    )

            # Check for short entry (fade an up streak)
            up_streak_end_index = -1
            temp_streak_len = 0
            temp_streak_dir = 0
            for i in range(len(closes) - 2, -1, -1): # Iterate backwards from second to last bar
                if closes[i+1] > closes[i]: # Up bar
                    if temp_streak_dir == 1:
                        temp_streak_len += 1
                    else:
                        temp_streak_dir = 1
                        temp_streak_len = 1
                else:
                    break # Streak broken
                
                if temp_streak_dir == 1 and temp_streak_len >= self.streak_period:
                    up_streak_end_index = i + 1 # The bar where the streak ended
                    break

            if up_streak_end_index != -1 and up_streak_end_index + self.continuation_fail_bars < len(closes):
                # Streak ended at up_streak_end_index. Now check bars after it.
                # All bars from up_streak_end_index+1 up to current must NOT be up bars
                failed_continuation = True
                for i in range(up_streak_end_index + 1, len(closes)):
                    if closes[i] > closes[i-1]: # If any bar continued the up trend
                        failed_continuation = False
                        break

                if failed_continuation and is_downtrend: # Only fade up streak if overall trend is down
                    # The current bar is the one confirming the failed continuation
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "up_streak_fade_failed_continuation",
                            "streak_length": temp_streak_len,
                            "fail_bars": self.continuation_fail_bars,
                            "current_close": ctx.bar.close,
                            "sma_long": sma_long
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic here, but could be used to adjust parameters
        pass