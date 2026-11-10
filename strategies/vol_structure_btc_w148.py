from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityEventPullback(Strategy):
    METADATA = {
        "name": "Volatility Event Pullback",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.pullback_threshold_factor = 0.5 # Percentage of recent range for pullback
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        # Indicators for trend and volatility
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.atr_period = 14

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[:period], period) # Use SMA for initial EMA
        if ema is None:
            return None
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if fast_ema is None or slow_ema is None or current_atr is None:
            return None

        # Trend identification using EMAs
        is_uptrend = fast_ema > slow_ema and closes[-1] > slow_ema
        is_downtrend = fast_ema < slow_ema and closes[-1] < slow_ema

        # Volatility Event Detection: Significant move followed by a pullback
        # Look for a recent strong move (expansion)
        # Use a rolling range based on ATR to define "significant"
        recent_high = max(highs[-self.volatility_period:])
        recent_low = min(lows[-self.volatility_period:])
        recent_range = recent_high - recent_low
        
        if recent_range == 0:
            return None # Avoid division by zero

        # Pullback condition
        pullback_amount = current_atr * self.pullback_threshold_factor

        # Long Entry: Uptrend + Pullback
        if is_uptrend and not ctx.has_position():
            # Check for a pullback from a recent high within the uptrend structure
            # Price should have dipped but still be above the slow EMA
            if current_price < recent_high - pullback_amount and current_price > slow_ema:
                # Ensure the low of the current bar is not breaking previous swing low (simple approximation)
                # This ensures the higher low structure is preserved
                if ctx.bar.low > self._sma(lows[-self.fast_ema_period:-1], self.fast_ema_period - 1): # Check against recent average low
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "uptrend_volatility_pullback_long",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "atr": current_atr,
                            "recent_high": recent_high,
                            "pullback_amount": pullback_amount,
                        }
                    )

        # Short Entry: Downtrend + Pullback
        if is_downtrend and not ctx.has_position():
            # Check for a pullback from a recent low within the downtrend structure
            # Price should have bounced but still be below the slow EMA
            if current_price > recent_low + pullback_amount and current_price < slow_ema:
                # Ensure the high of the current bar is not breaking previous swing high (simple approximation)
                # This ensures the lower high structure is preserved
                if ctx.bar.high < self._sma(highs[-self.fast_ema_period:-1], self.fast_ema_period - 1): # Check against recent average high
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "downtrend_volatility_pullback_short",
                            "current_price": current_price,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "atr": current_atr,
                            "recent_low": recent_low,
                            "pullback_amount": pullback_amount,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, ensuring we wait self.cooldown_bars from this point
        self.last_trade_bar_index = ctx.bar_index
        # Log the close for debugging/analysis
        ctx.log(f"Position closed: {position['direction']} {position['entry_price']} -> {position['exit_price']} ({position['return_bps']:.2f} bps) after {position['bars_held']} bars. Reason: {position.get('exit_reason', 'unknown')}")