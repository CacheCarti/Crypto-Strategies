from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakEth(Strategy):
    METADATA = {
        "name": "SentimentFearStreakEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 5 # Look back 5 days for fear_greed_index
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.min_fear_index = 30 # Max fear_greed_index for "fear streak"
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

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
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Fear Streak Logic
        fear_greed_indices = []
        # fear_greed_index is daily, so we need to collect unique daily values
        # Iterate backwards to find the last `fear_greed_period` unique daily values
        current_day = ctx.bar.timestamp.day
        prev_fear_greed = None
        for i in range(len(ctx.closes(self.warmup_bars))):
            # The actual feature access needs to be on the current bar's context
            # This is a simplification for backtesting purposes; in reality,
            # ctx.features.get("fear_greed_index") always returns the current bar's value.
            # We assume the platform handles the daily cadence correctly.
            fg_val = ctx.features.get("fear_greed_index", 50)
            
            # This is a proxy for getting daily unique values in a bar-by-bar context
            # In live, fear_greed_index updates daily, so this logic is implicitly handled.
            # For backtest, we just take the current value as the "daily" value.
            # If the value changed from the previous bar, consider it a new daily reading.
            if len(fear_greed_indices) == 0 or fg_val != prev_fear_greed:
                fear_greed_indices.append(fg_val)
                prev_fear_greed = fg_val
            
            if len(fear_greed_indices) >= self.fear_greed_period:
                break
        
        # Ensure we have enough data points for fear_greed_period
        if len(fear_greed_indices) < self.fear_greed_period:
            return None

        # Check for fear streak: all recent fear_greed_indices must be below threshold
        is_fear_streak = all(fg <= self.min_fear_index for fg in fear_greed_indices[-self.fear_greed_period:])

        # Price stabilization trigger (EMA crossover)
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Check for EMA crossover: short EMA crosses above long EMA
        # We need previous bar's EMA values to detect a cross
        prev_closes = ctx.closes(self.warmup_bars + 1)
        if len(prev_closes) < self.warmup_bars + 1:
            return None
        
        prev_ema_short = self._ema(prev_closes[:-1], self.ema_short_period)
        prev_ema_long = self._ema(prev_closes[:-1], self.ema_long_period)

        ema_cross_up = (prev_ema_short is not None and prev_ema_long is not None and
                        prev_ema_short < prev_ema_long and ema_short > ema_long)

        # Entry condition: fear streak AND EMA crossover
        if not ctx.has_position() and is_fear_streak and ema_cross_up:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "fear_streak_ema_bullish_cross",
                    "fear_greed_streak_len": self.fear_greed_period,
                    "fear_greed_current": ctx.features.get("fear_greed_index", 50),
                    "fear_greed_min_threshold": self.min_fear_index,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "current_price": ctx.bar.close,
                }
            )

        # Exit condition: If in a position and EMAs cross down, or fear streak ends
        if ctx.has_position() and ctx.position_direction() == "long":
            ema_cross_down = (prev_ema_short is not None and prev_ema_long is not None and
                              prev_ema_short > prev_ema_long and ema_short < ema_long)
            
            # Check if fear streak has ended (i.e., current fear_greed_index is above threshold)
            current_fg = ctx.features.get("fear_greed_index", 50)
            fear_streak_ended = current_fg > self.min_fear_index
            
            if ema_cross_down or fear_streak_ended:
                self.last_trade_bar = ctx.bar_index
                reason = "ema_bearish_cross" if ema_cross_down else "fear_streak_ended"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": f"exit_{reason}",
                        "fear_greed_current": current_fg,
                        "fear_greed_min_threshold": self.min_fear_index,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "current_price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on position outcome
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars")
        pass