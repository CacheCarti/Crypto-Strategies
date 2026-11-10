from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PullbackTrendContinuation(Strategy):
    METADATA = {
        "name": "Pullback Trend Continuation",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Intended avg hold time around 3 hours
        "warmup_bars": 50,  # Need enough bars for EMA and trend detection
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.trend_lookback = 10 # Bars to detect higher highs/lows or lower highs/lows
        self.pullback_threshold_factor = 0.005 # 0.5% pullback from high/low
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _is_uptrend(self, highs, lows, lookback):
        if len(highs) < lookback or len(lows) < lookback:
            return False
        # Check for higher highs and higher lows
        hh_count = 0
        hl_count = 0
        for i in range(1, lookback):
            if highs[-i] > highs[-i-1]:
                hh_count += 1
            if lows[-i] > lows[-i-1]:
                hl_count += 1
        return hh_count >= lookback * 0.7 and hl_count >= lookback * 0.7 # 70% of bars show higher highs/lows

    def _is_downtrend(self, highs, lows, lookback):
        if len(highs) < lookback or len(lows) < lookback:
            return False
        # Check for lower highs and lower lows
        lh_count = 0
        ll_count = 0
        for i in range(1, lookback):
            if highs[-i] < highs[-i-1]:
                lh_count += 1
            if lows[-i] < lows[-i-1]:
                ll_count += 1
        return lh_count >= lookback * 0.7 and ll_count >= lookback * 0.7 # 70% of bars show lower highs/lows

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Determine trend based on EMAs
        is_ema_uptrend = ema_fast > ema_slow and closes[-1] > ema_fast
        is_ema_downtrend = ema_fast < ema_slow and closes[-1] < ema_fast

        # Determine trend based on OHLC structure
        is_ohcl_uptrend = self._is_uptrend(highs, lows, self.trend_lookback)
        is_ohcl_downtrend = self._is_downtrend(highs, lows, self.trend_lookback)

        # Long Entry: Uptrend detected, waiting for a pullback to EMA_fast
        if not ctx.has_position() and is_ema_uptrend and is_ohcl_uptrend:
            # Check for pullback: current price pulled back towards or below fast EMA
            # And current low is above slow EMA
            if current_price < ema_fast and ctx.bar.low > ema_slow:
                # Ensure it's a recent pullback, not a sustained dip
                # Check if previous bar was above EMA_fast
                if closes[-2] > ema_fast:
                    # Check for "clean event boundary" - a significant move up before pullback
                    # E.g., previous bar closed significantly higher than its open, or a strong upward momentum
                    prev_bar_range = closes[-2] - closes[-3]
                    if prev_bar_range > 0 and (prev_bar_range / closes[-3]) > self.pullback_threshold_factor:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "pullback_to_ema_fast_in_uptrend",
                                "current_price": current_price,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "prev_bar_close": closes[-2],
                                "prev_bar_range_pct": (prev_bar_range / closes[-3]) * 100,
                                "ohcl_uptrend": is_ohcl_uptrend
                            }
                        )

        # Short Entry: Downtrend detected, waiting for a pullback to EMA_fast
        elif not ctx.has_position() and is_ema_downtrend and is_ohcl_downtrend:
            # Check for pullback: current price pulled back towards or above fast EMA
            # And current high is below slow EMA
            if current_price > ema_fast and ctx.bar.high < ema_slow:
                # Ensure it's a recent pullback
                # Check if previous bar was below EMA_fast
                if closes[-2] < ema_fast:
                    # Check for "clean event boundary" - a significant move down before pullback
                    # E.g., previous bar closed significantly lower than its open
                    prev_bar_range = closes[-3] - closes[-2] # Open - Close for down bar
                    if prev_bar_range > 0 and (prev_bar_range / closes[-3]) > self.pullback_threshold_factor:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "pullback_to_ema_fast_in_downtrend",
                                "current_price": current_price,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "prev_bar_close": closes[-2],
                                "prev_bar_range_pct": (prev_bar_range / closes[-3]) * 100,
                                "ohcl_downtrend": is_ohcl_downtrend
                            }
                        )

        # Exit logic (if position is open)
        if ctx.has_position():
            # If the trend reverses or gets choppy, consider exiting
            if ctx.position_direction() == "long":
                # Exit if fast EMA crosses below slow EMA, or if price breaks below slow EMA
                if ema_fast < ema_slow or current_price < ema_slow:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_trend_reversal_or_breakdown",
                            "current_price": current_price,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "trend_regime": ctx.market.get("trend_regime", "neutral"),
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit if fast EMA crosses above slow EMA, or if price breaks above slow EMA
                if ema_fast > ema_slow or current_price > ema_slow:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_trend_reversal_or_breakdown",
                            "current_price": current_price,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "trend_regime": ctx.market.get("trend_regime", "neutral"),
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown
        self.last_trade_bar = ctx.bar_index
        pass