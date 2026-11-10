from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyTransitionSOL(Strategy):
    METADATA = {
        "name": "Weekly Transition SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds, up to Monday 21 UTC
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # 24 bars for 24 hours
        self.volume_avg_period = 12
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        volumes = ctx.volumes(self.volume_avg_period)

        if len(closes) < self.lookback_period + 1 or len(volumes) < self.volume_avg_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime filter: Only trade in normal or volatile regimes, not crisis
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
            return None

        # Only consider trades on Sunday UTC 23:00 (the last bar of Sunday)
        # to capture the immediate move after the weekly open, or Monday 00:00 UTC
        # If we signal on Sunday 23:00, it's for the bar closing at 23:00, which reflects the move *into* the close.
        # The prompt says "first decisive move after Sunday UTC reopen", which is Monday 00:00 UTC.
        # So we look at the bar closing at Monday 00:00 UTC.
        if not (current_weekday == 6 and current_hour == 23) and \
           not (current_weekday == 0 and current_hour == 0):
            if ctx.has_position() and current_weekday == 0 and current_hour == 20: # Close before Monday 21 UTC
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "monday_eod_exit", "current_hour": current_hour})
            return None
        
        # Ensure we only trigger once per week
        if ctx.has_position():
            return None
        
        # Calculate average volume
        avg_volume = self._sma(volumes, self.volume_avg_period)
        if avg_volume is None:
            return None

        # Look at the price change from the last bar (Sunday 22 UTC close to Sunday 23 UTC close)
        # or (Sunday 23 UTC close to Monday 00 UTC close)
        price_change = closes[-1] - closes[-2]
        volume_current = volumes[-1]

        # Check for significant move and volume confirmation
        # A 0.5% move in an hour is considered significant for SOL
        significant_move_threshold = closes[-2] * 0.005 # 0.5%
        volume_confirm_ratio = 1.2 # Current bar volume must be 20% higher than average

        signal_direction = None
        reason = ""
        
        # Check for Sunday 23:00 UTC bar (reflects move into weekly close)
        if current_weekday == 6 and current_hour == 23:
            if price_change > significant_move_threshold and volume_current > avg_volume * volume_confirm_ratio:
                signal_direction = "long"
                reason = "sunday_23_utc_bullish_transition"
            elif price_change < -significant_move_threshold and volume_current > avg_volume * volume_confirm_ratio:
                signal_direction = "short"
                reason = "sunday_23_utc_bearish_transition"
        
        # Check for Monday 00:00 UTC bar (reflects move after weekly open)
        elif current_weekday == 0 and current_hour == 0:
            if price_change > significant_move_threshold and volume_current > avg_volume * volume_confirm_ratio:
                signal_direction = "long"
                reason = "monday_00_utc_bullish_continuation"
            elif price_change < -significant_move_threshold and volume_current > avg_volume * volume_confirm_ratio:
                signal_direction = "short"
                reason = "monday_00_utc_bearish_continuation"

        if signal_direction:
            # Check for non-spike bar: ensure the body of the candle is substantial
            # (e.g., close is not too far from open if it's a spike rejection)
            open_price = ctx.opens(1)[-1]
            high_price = ctx.highs(1)[-1]
            low_price = ctx.lows(1)[-1]
            
            # Simple check: close should be near high for long, near low for short
            if signal_direction == "long" and (closes[-1] - open_price) < (high_price - low_price) * 0.3: # Body less than 30% of range
                return None # Likely a spike, not a sustained move
            if signal_direction == "short" and (open_price - closes[-1]) < (high_price - low_price) * 0.3: # Body less than 30% of range
                return None # Likely a spike, not a sustained move

            # Further confidence from broader market trends
            btc_return = ctx.features.get("btc_return_pct", 0.0)
            eth_return = ctx.features.get("eth_return_pct", 0.0)
            
            confidence = 0.6 # Base confidence
            if (signal_direction == "long" and btc_return > 0.5 and eth_return > 0.5) or \
               (signal_direction == "short" and btc_return < -0.5 and eth_return < -0.5):
                confidence = 0.8 # Higher confidence if broader market aligns

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "price_change_pct": (price_change / closes[-2]) * 100,
                    "volume_current": volume_current,
                    "volume_avg": avg_volume,
                    "btc_return_pct": btc_return,
                    "eth_return_pct": eth_return,
                    "regime": ctx.market.get("regime", "NORMAL")
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to ensure cooldown is applied correctly
        # This is already handled in on_bar, but could be useful for adaptive logic.
        pass