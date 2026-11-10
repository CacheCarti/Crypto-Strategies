from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyCloseDrift(Strategy):
    METADATA = {
        "name": "Weekly Close Drift BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 72 * 3600,  # Hold from Friday close to Sunday evening (approx 72 hours)
        "warmup_bars": 100,  # Need enough data for EMAs and volume calculations
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_short_period = 20
        self.ema_long_period = 50
        self.volume_avg_period = 20
        self.last_signal_bar = -1
        self.cooldown_bars = 48  # Wait 48 hours (48 bars) after a trade to prevent overtrading

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period  # Simple average for the initial period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_long_period + 1)
        volumes = ctx.volumes(self.volume_avg_period)

        if len(closes) < self.METADATA["warmup_bars"] or len(volumes) < self.volume_avg_period:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Only consider trades around Friday close
        # Friday is weekday() == 4. We want to check the last 6 hours of Friday (UTC)
        # So, Friday 18:00 UTC to Friday 23:00 UTC (hours 18, 19, 20, 21, 22, 23)
        is_friday_close_window = (ctx.bar.timestamp.weekday() == 4 and ctx.bar.timestamp.hour >= 18)

        if not is_friday_close_window:
            return None

        # Calculate EMAs
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        if ema_short is None or ema_long is None:
            return None

        current_close = ctx.bar.close
        previous_close = closes[-2] # Get the close from the previous bar

        # Volume confirmation: current volume significantly above average
        avg_volume = self._sma(volumes, self.volume_avg_period)
        if avg_volume is None:
            return None
        volume_ratio = ctx.bar.volume / avg_volume

        # Sentiment filter: only trade if Fear & Greed is not "Extreme Greed"
        # We want to avoid buying into unsustainable euphoria for long positions
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")
        
        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: Bullish close on Friday, above EMAs, high volume, not extreme greed
        if (current_close > previous_close and                 # Price closed higher than previous bar
            current_close > ema_short and                      # Current price above short EMA
            ema_short > ema_long and                           # Short EMA above long EMA (bullish trend)
            volume_ratio > 1.5 and                             # Strong volume confirmation
            fear_greed_class not in ["Extreme Greed", "Greed"]): # Avoid extreme greed
            
            signal_direction = "long"
            confidence = min(0.7 + (volume_ratio - 1.5) * 0.1, 1.0) # Scale confidence with volume
            reason = "weekly_close_long_ema_cross_high_vol_no_extreme_greed"

        # Short condition: Bearish close on Friday, below EMAs, high volume, not extreme fear
        elif (current_close < previous_close and               # Price closed lower than previous bar
              current_close < ema_short and                    # Current price below short EMA
              ema_short < ema_long and                         # Short EMA below long EMA (bearish trend)
              volume_ratio > 1.5 and                           # Strong volume confirmation
              fear_greed_class not in ["Extreme Fear", "Fear"]): # Avoid extreme fear
            
            signal_direction = "short"
            confidence = min(0.7 + (volume_ratio - 1.5) * 0.1, 1.0) # Scale confidence with volume
            reason = "weekly_close_short_ema_cross_high_vol_no_extreme_fear"
        
        if signal_direction and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_close": current_close,
                    "previous_close": previous_close,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "volume_ratio": volume_ratio,
                    "fear_greed_class": fear_greed_class,
                    "timestamp": ctx.bar.timestamp.isoformat()
                }
            )
        
        # If we have a position, we only want to exit on Sunday evening
        if ctx.has_position():
            # Sunday is weekday() == 6. We want to exit on Sunday evening (UTC hours 18-23)
            is_sunday_evening_exit_window = (ctx.bar.timestamp.weekday() == 6 and ctx.bar.timestamp.hour >= 18)
            
            if is_sunday_evening_exit_window:
                # Ensure we actually entered on a Friday close and are now within the intended hold period
                # (This check is implicitly handled by the horizon_seconds, but good to be explicit for clarity)
                
                # Exit the position
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8, # High confidence in exiting as per strategy rules
                    metadata={
                        "reason": "sunday_evening_exit_window",
                        "current_close": current_close,
                        "timestamp": ctx.bar.timestamp.isoformat()
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close details or adapt future trading
        pass