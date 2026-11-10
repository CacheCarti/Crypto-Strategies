from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendReversalMondayOpen(Strategy):
    METADATA = {
        "name": "Weekend Reversal Monday Open",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-3 hours, but can extend
        "warmup_bars": 72, # 3 days of 1-hour bars for weekend analysis
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_return = 0.0
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.cooldown_bars = 12 # Wait 12 hours after a trade
        self.last_signal_direction = None
        self.weekend_period = 48 # 48 hours for weekend calculation

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + self.weekend_period)
        if len(closes) < self.METADATA["warmup_bars"] + self.weekend_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Cooldown logic: if a trade recently happened, wait
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate weekend return on Sunday 23:00 UTC (end of weekend)
        # Store for use on Monday
        if current_weekday == 6 and current_hour == 23: # Sunday 23:00 UTC
            # Calculate return from Friday 23:00 UTC to Sunday 23:00 UTC
            # Assuming 1-hour bars, that's 48 bars
            if len(closes) >= self.weekend_period:
                friday_close = closes[-(self.weekend_period + 1)] # Close 48 bars ago
                sunday_close = closes[-1]
                self.weekend_return = (sunday_close - friday_close) / friday_close
            else:
                self.weekend_return = 0.0
            return None # Don't trade on Sunday night

        # Entry logic: Monday, during US trading hours (e.g., 13:00-21:00 UTC)
        # Only if no position is currently open
        if not ctx.has_position() and current_weekday == 0 and 13 <= current_hour <= 21: # Monday, US hours
            # Use a short-term EMA cross to confirm Monday's direction
            fast_ema_period = 5
            slow_ema_period = 10
            
            fast_ema = self._sma(closes, fast_ema_period) # Using SMA as a proxy for EMA for simplicity/performance
            slow_ema = self._sma(closes, slow_ema_period) # Using SMA as a proxy for EMA for simplicity/performance

            if fast_ema is None or slow_ema is None:
                return None

            # Check if previous bar had a cross or if current bar is already crossed
            prev_closes = ctx.closes(slow_ema_period + 1)
            prev_fast_ema = self._sma(prev_closes[:-1], fast_ema_period)
            prev_slow_ema = self._sma(prev_closes[:-1], slow_ema_period)

            # Only consider a trade if there's a clear weekend move and a Monday confirmation
            if abs(self.weekend_return) > 0.015: # Significant weekend move (1.5%)
                signal_direction = None
                reason = ""
                confidence = 0.0

                # Long signal: Weekend down, Monday EMA cross up
                if self.weekend_return < -0.015: # Weekend was significantly down
                    if fast_ema > slow_ema and (prev_fast_ema is None or prev_fast_ema <= prev_slow_ema):
                        signal_direction = "long"
                        reason = "monday_reversal_weekend_selloff_ema_cross_up"
                        # Confidence based on magnitude of weekend move and current market regime
                        confidence = min(0.5 + abs(self.weekend_return) * 10, 0.9)
                        if ctx.market.get("trend_regime") == "bull": confidence = min(confidence * 1.2, 0.95)

                # Short signal: Weekend up, Monday EMA cross down
                elif self.weekend_return > 0.015: # Weekend was significantly up
                    if fast_ema < slow_ema and (prev_fast_ema is None or prev_fast_ema >= prev_slow_ema):
                        signal_direction = "short"
                        reason = "monday_reversal_weekend_rally_ema_cross_down"
                        confidence = min(0.5 + abs(self.weekend_return) * 10, 0.9)
                        if ctx.market.get("trend_regime") == "bear": confidence = min(confidence * 1.2, 0.95)

                if signal_direction:
                    # Additional filter: check Fear & Greed Index
                    fear_greed = ctx.features.get("fear_greed_index", 50)
                    if signal_direction == "long" and fear_greed > 70: # Too much greed for a long reversal
                        return None
                    if signal_direction == "short" and fear_greed < 30: # Too much fear for a short reversal
                        return None

                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = signal_direction
                    self.weekend_return = 0.0 # Reset for next weekend

                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "weekend_return_pct": self.weekend_return * 100,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": ctx.bar.close,
                            "fear_greed_index": fear_greed,
                            "market_trend_regime": ctx.market.get("trend_regime"),
                        }
                    )
        
        # Exit logic: if we have a position, consider exiting if the EMA cross reverses
        if ctx.has_position():
            fast_ema_period = 5
            slow_ema_period = 10
            
            fast_ema = self._sma(closes, fast_ema_period)
            slow_ema = self._sma(closes, slow_ema_period)

            if fast_ema is None or slow_ema is None:
                return None

            # Get previous EMA values to detect a cross
            prev_closes = ctx.closes(slow_ema_period + 1)
            prev_fast_ema = self._sma(prev_closes[:-1], fast_ema_period)
            prev_slow_ema = self._sma(prev_closes[:-1], slow_ema_period)

            if ctx.position_direction() == "long":
                # Exit long if fast EMA crosses below slow EMA
                if fast_ema < slow_ema and (prev_fast_ema is None or prev_fast_ema >= prev_slow_ema):
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "flat"
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "ema_cross_down_exit_long",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": ctx.bar.close,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if fast EMA crosses above slow EMA
                if fast_ema > slow_ema and (prev_fast_ema is None or prev_fast_ema <= prev_slow_ema):
                    self.last_trade_bar = ctx.bar_index
                    self.last_signal_direction = "flat"
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "ema_cross_up_exit_short",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": ctx.bar.close,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset weekend_return and last_signal_direction on close for safety, though it's handled in on_bar
        self.weekend_return = 0.0
        self.last_signal_direction = None
        # Could use this for adaptive sizing, but keeping it simple for now.
        pass