from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaHandoffBTC(Strategy):
    METADATA = {
        "name": "Asia Handoff BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # 1 hour bar, aim for 1-2 bars hold
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_bar_index = -1
        self.last_signal_bar = -1
        self.cooldown_bars = 5 # Cooldown to prevent overtrading

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        # Define Asia session as UTC 0-8 (inclusive of 0, exclusive of 8 for measuring impulse)
        # Handoff occurs around 8 UTC
        
        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Exit existing position before the defined session boundary (e.g., before UTC 8)
        # Or if the position has been open for too long (1-2 bars for 1h chart)
        if ctx.has_position():
            # Exit if past the intended hold time or approaching the session handoff
            if ctx.bar_index - self.entry_bar_index >= self.declared_hold_seconds // 3600 + 1 or current_hour >= 8:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_handoff_or_max_hold", "current_hour": current_hour, "bars_held": ctx.bar_index - self.entry_bar_index})
            return None # Hold position

        # Entry logic: Only look for entry signals during the Asia session (e.g., 0-7 UTC)
        # The strategy measures impulse at hour 7, then enters at hour 8 if confirmed.
        # This means we evaluate the session impulse when we have the full 0-7 hours.
        # So, the entry signal itself will fire at 8 UTC.

        # Calculate impulse based on the 0-7 UTC session
        # We need at least 8 bars (0, 1, ..., 7) to measure the session's direction.
        # This implies we need to look back at least 8 bars from the current 8 UTC bar.
        
        # Check if current bar is 8 UTC (the handoff hour for entry)
        if current_hour == 8:
            # Need to get prices from the Asia session (UTC 0 to 7)
            # This means we need bars from ctx.bar_index - 8 to ctx.bar_index - 1
            # (assuming 1h bars)
            
            # Fetch relevant closes for the Asia session
            # We need to ensure we have enough history to cover the previous 8 bars.
            if ctx.bar_index < 8:
                return None # Not enough history for Asia session

            # Get the closes for the previous 8 hours (0-7 UTC of the previous day/session)
            # This is a simplification; a more robust solution would track actual UTC days.
            # For this simple approach, we'll just check if the last 8 bars represent a directional move.
            
            # Let's use a simpler approach: check the previous 8 bars (from bar_index-8 to bar_index-1)
            # These would be the bars corresponding to the 0-7 UTC period, if the current bar is 8 UTC.
            
            session_closes = ctx.closes(9) # Get 9 bars to compare current close with previous 8
            session_opens = ctx.opens(9)
            
            if len(session_closes) < 9:
                return None

            # Price at the start of the Asia session (0 UTC) and end (7 UTC)
            # For simplicity, we'll use the open of the first bar and close of the last bar of the "session"
            # The "session" here is defined as the 8 bars *before* the current 8 UTC bar.
            
            # This means session_opens[0] is the open of ctx.bar_index - 8
            # and session_closes[-2] is the close of ctx.bar_index - 1 (the 7 UTC bar)

            session_start_price = session_opens[0]
            session_end_price = session_closes[-2] # Close of the 7 UTC bar

            price_change = session_end_price - session_start_price
            
            # Use a simple moving average to confirm trend direction
            # We need a longer SMA for confirmation, e.g., 20 bars
            sma_period = 20
            sma_values = ctx.closes(sma_period)
            sma = self._sma(sma_values, sma_period)

            if sma is None:
                return None

            # Entry condition:
            # 1. Asia session impulse is significantly bullish/bearish
            # 2. Current price (close of 8 UTC bar) confirms the direction relative to a longer SMA.
            # 3. Prevent repeated entries on the same day/session.
            
            # Threshold for impulse strength (e.g., 0.1% move over 8 hours)
            impulse_threshold_pct = 0.001 # 0.1% move
            impulse_threshold_abs = ctx.bar.close * impulse_threshold_pct

            # Check if the session impulse was bullish and current price is above SMA
            if price_change > impulse_threshold_abs and ctx.bar.close > sma:
                self.entry_bar_index = ctx.bar_index
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "asia_session_bullish_handoff",
                        "session_start_price": session_start_price,
                        "session_end_price": session_end_price,
                        "price_change_abs": price_change,
                        "current_close": ctx.bar.close,
                        "sma_20": sma,
                    }
                )
            # Check if the session impulse was bearish and current price is below SMA
            elif price_change < -impulse_threshold_abs and ctx.bar.close < sma:
                self.entry_bar_index = ctx.bar_index
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "asia_session_bearish_handoff",
                        "session_start_price": session_start_price,
                        "session_end_price": session_end_price,
                        "price_change_abs": price_change,
                        "current_close": ctx.bar.close,
                        "sma_20": sma,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry_bar_index to allow new entries after a position closes
        self.entry_bar_index = -1
        # The cooldown logic handles subsequent entries
        pass