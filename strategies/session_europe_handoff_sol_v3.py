from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeSessionHandoff(Strategy):
    METADATA = {
        "name": "Sol Europe Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold through next session handoff
        "warmup_bars": 30,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM (exclusive)
        self.exit_hour = 12        # UTC 12 PM (end of European session)
        self.min_bars_since_trade = 6 # Cooldown period in bars (6 hours for 1h bars)
        self.last_trade_bar_index = -self.min_bars_since_trade

        # Parameters for detecting impulse and follow-through
        self.impulse_period = 4  # Lookback for impulse within the entry window
        self.follow_through_period = 2 # Lookback for follow-through after entry window
        self.min_impulse_strength = 0.005 # Minimum price movement % for impulse
        self.max_impulse_reversal = 0.002 # Max reversal % before rejecting impulse
        self.vol_sma_period = 10 # For volume confirmation

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for position close at the end of the session
        if ctx.has_position() and current_hour == self.exit_hour:
            # Check if the current bar is the first bar of the exit_hour
            # to ensure we only trigger the exit once.
            prev_hour = ctx.bar.timestamp.hour - 1 if ctx.bar.timestamp.hour > 0 else 23
            if prev_hour != self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_boundary_exit",
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"] if ctx.position else "N/A"
                })

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar_index + self.min_bars_since_trade:
            return None

        # Only consider entries during the specified European morning hours
        if self.entry_hour_start <= current_hour < self.entry_hour_end and not ctx.has_position():
            # Ensure we only try to enter once per session
            # Check if we are on the first bar of the entry_hour_start
            if current_hour == self.entry_hour_start and ctx.bar.timestamp.hour == self.entry_hour_start:
                # Calculate impulse over the last self.impulse_period bars
                if len(closes) < self.impulse_period + 1:
                    return None

                # Calculate price change over the impulse period
                price_change = (closes[-1] - closes[-self.impulse_period]) / closes[-self.impulse_period]
                
                # Check for significant volume
                avg_vol = self._sma(volumes, self.vol_sma_period)
                if avg_vol is None or volumes[-1] < avg_vol * 1.2: # Current bar volume 20% above average
                    return None

                # Detect impulse direction
                if price_change > self.min_impulse_strength: # Bullish impulse
                    # Check for follow-through confirmation after the impulse period
                    # This implies waiting for the next bar or two after the initial impulse detection
                    # For simplicity, we'll check for positive closing price relative to the average of the impulse period
                    # This is a 'fast trigger variant' as requested, checking immediately after impulse.
                    
                    # Ensure price hasn't reversed too much from its peak during impulse
                    peak_price_impulse = max(highs[-self.impulse_period:])
                    if (peak_price_impulse - closes[-1]) / closes[-1] > self.max_impulse_reversal:
                        return None # Too much reversal

                    # Follow-through: current close is higher than the open of the impulse period and higher than previous close
                    if closes[-1] > opens[-self.impulse_period] and closes[-1] > closes[-2]:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "europe_session_bullish_impulse_follow_through",
                            "current_hour": current_hour,
                            "price_change_pct": price_change * 100,
                            "current_close": closes[-1],
                            "volume_ratio": volumes[-1] / avg_vol,
                            "impulse_period": self.impulse_period
                        })

                elif price_change < -self.min_impulse_strength: # Bearish impulse
                    # Check for follow-through confirmation
                    # Ensure price hasn't reversed too much from its trough during impulse
                    trough_price_impulse = min(lows[-self.impulse_period:])
                    if (closes[-1] - trough_price_impulse) / closes[-1] > self.max_impulse_reversal:
                        return None # Too much reversal

                    # Follow-through: current close is lower than the open of the impulse period and lower than previous close
                    if closes[-1] < opens[-self.impulse_period] and closes[-1] < closes[-2]:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "europe_session_bearish_impulse_follow_through",
                            "current_hour": current_hour,
                            "price_change_pct": price_change * 100,
                            "current_close": closes[-1],
                            "volume_ratio": volumes[-1] / avg_vol,
                            "impulse_period": self.impulse_period
                        })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log for debugging or adaptive logic if needed
        pass