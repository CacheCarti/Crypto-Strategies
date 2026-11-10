from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrend(Strategy):
    METADATA = {
        "name": "Volatility Quiet Trend",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.volatility_threshold_multiplier = 1.5 # How many ATRs above average is "high" volatility
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow trading immediately

        # Store previous ATR to detect volatility expansion
        self.prev_atr = 0.0

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate EMAs for trend direction
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position(): # Allow exiting during cooldown
                pass
            else:
                self.prev_atr = current_atr # Update ATR even if on cooldown
                return None

        # Volatility regime detection
        # We need a longer history for average ATR to classify "low" volatility
        atr_history_for_avg = [self._atr(ctx.highs(i+1), ctx.lows(i+1), ctx.closes(i+1), self.atr_period) 
                               for i in range(self.atr_period + 1, len(ctx.closes())) if self._atr(ctx.highs(i+1), ctx.lows(i+1), ctx.closes(i+1), self.atr_period) is not None]
        
        if len(atr_history_for_avg) < self.atr_period: # Need enough history to compute average ATR
            self.prev_atr = current_atr
            return None

        average_atr = sum(atr_history_for_avg[-self.atr_period:]) / self.atr_period
        
        is_low_volatility = current_atr < average_atr * 0.8 # Define low volatility as significantly below average
        is_volatility_expansion = current_atr > self.prev_atr * self.volatility_threshold_multiplier and current_atr > average_atr # Significant jump from prev ATR and above average

        current_price = ctx.bar.close

        # Exit logic: Exit on volatility expansion
        if ctx.has_position():
            if is_volatility_expansion:
                reason = f"volatility_expansion_exit_from_{ctx.position_direction()}"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": reason,
                    "current_atr": current_atr,
                    "prev_atr": self.prev_atr,
                    "average_atr": average_atr,
                    "price": current_price
                })

        # Entry logic: Only enter in low volatility regimes and when trend is clear
        if not ctx.has_position() and is_low_volatility:
            # Long entry: Fast EMA above Slow EMA, price above both EMAs
            if ema_fast > ema_slow and current_price > ema_fast:
                # Ensure it's a new cross or a sustained trend
                prev_ema_fast = self._ema(ctx.closes(self.ema_period_fast + 1), self.ema_period_fast)
                prev_ema_slow = self._ema(ctx.closes(self.ema_period_slow + 1), self.ema_period_slow)

                if prev_ema_fast is not None and prev_ema_slow is not None:
                    # Only enter if it's a recent bullish cross or continuation of a strong trend
                    if ema_fast > ema_slow and prev_ema_fast <= prev_ema_slow: # Bullish cross
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "low_vol_ema_bull_cross_trend_entry",
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_atr": current_atr,
                            "average_atr": average_atr,
                            "price": current_price
                        })
                    elif ema_fast > ema_slow and current_price > ema_fast * 1.001: # Strong trend continuation
                         self.last_trade_bar = ctx.bar_index
                         return ctx.signal("long", confidence=0.6, metadata={
                            "reason": "low_vol_ema_bull_trend_continuation",
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_atr": current_atr,
                            "average_atr": average_atr,
                            "price": current_price
                        })


            # Short entry: Fast EMA below Slow EMA, price below both EMAs
            elif ema_fast < ema_slow and current_price < ema_fast:
                prev_ema_fast = self._ema(ctx.closes(self.ema_period_fast + 1), self.ema_period_fast)
                prev_ema_slow = self._ema(ctx.closes(self.ema_period_slow + 1), self.ema_period_slow)

                if prev_ema_fast is not None and prev_ema_slow is not None:
                    # Only enter if it's a recent bearish cross or continuation of a strong trend
                    if ema_fast < ema_slow and prev_ema_fast >= prev_ema_slow: # Bearish cross
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "low_vol_ema_bear_cross_trend_entry",
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_atr": current_atr,
                            "average_atr": average_atr,
                            "price": current_price
                        })
                    elif ema_fast < ema_slow and current_price < ema_fast * 0.999: # Strong trend continuation
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.6, metadata={
                            "reason": "low_vol_ema_bear_trend_continuation",
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_atr": current_atr,
                            "average_atr": average_atr,
                            "price": current_price
                        })
        
        self.prev_atr = current_atr
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close
        pass