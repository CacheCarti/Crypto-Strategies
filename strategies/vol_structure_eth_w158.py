from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PullbackTrendContinuation(Strategy):
    METADATA = {
        "name": "Pullback Trend Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.trend_period = 20
        self.pullback_threshold = 0.005 # 0.5% pullback for entry
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _is_uptrend(self, closes, highs, lows, period):
        if len(closes) < period:
            return False
        # Check for higher highs and higher lows in the recent past
        recent_highs = highs[-period:]
        recent_lows = lows[-period:]

        # Simple check: last high > first high, last low > first low
        if recent_highs[-1] > recent_highs[0] and recent_lows[-1] > recent_lows[0]:
            return True
        return False

    def _is_downtrend(self, closes, highs, lows, period):
        if len(closes) < period:
            return False
        # Check for lower highs and lower lows in the recent past
        recent_highs = highs[-period:]
        recent_lows = lows[-period:]

        # Simple check: last high < first high, last low < first low
        if recent_highs[-1] < recent_highs[0] and recent_lows[-1] < recent_lows[0]:
            return True
        return False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Long Entry Logic:
        # 1. Fast EMA above Slow EMA (uptrend confirmation)
        # 2. Price is currently pulling back towards the fast EMA (but not below)
        # 3. Higher highs and higher lows pattern in the last `trend_period` bars
        if not ctx.has_position():
            if fast_ema > slow_ema: # Uptrend
                if closes[-2] > fast_ema and current_price < fast_ema: # Pullback occurred
                    if fast_ema - current_price < self.pullback_threshold * current_price: # Small pullback, near EMA
                        if self._is_uptrend(closes, highs, lows, self.trend_period):
                            self.last_signal_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                metadata={
                                    "reason": "uptrend_pullback_continuation",
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "current_price": current_price,
                                    "pullback_threshold": self.pullback_threshold
                                }
                            )

            # Short Entry Logic:
            # 1. Fast EMA below Slow EMA (downtrend confirmation)
            # 2. Price is currently pulling back towards the fast EMA (but not above)
            # 3. Lower highs and lower lows pattern in the last `trend_period` bars
            elif fast_ema < slow_ema: # Downtrend
                if closes[-2] < fast_ema and current_price > fast_ema: # Pullback occurred
                    if current_price - fast_ema < self.pullback_threshold * current_price: # Small pullback, near EMA
                        if self._is_downtrend(closes, highs, lows, self.trend_period):
                            self.last_signal_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                metadata={
                                    "reason": "downtrend_pullback_continuation",
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "current_price": current_price,
                                    "pullback_threshold": self.pullback_threshold
                                }
                            )
        
        # Exit Logic (if position is open)
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if fast EMA crosses below slow EMA or price breaks significantly below fast EMA
                if fast_ema < slow_ema or current_price < fast_ema * (1 - self.pullback_threshold * 1.5):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_trend_reversal_or_breakdown",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if fast EMA crosses above slow EMA or price breaks significantly above fast EMA
                if fast_ema > slow_ema or current_price > fast_ema * (1 + self.pullback_threshold * 1.5):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_trend_reversal_or_breakdown",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy
        pass