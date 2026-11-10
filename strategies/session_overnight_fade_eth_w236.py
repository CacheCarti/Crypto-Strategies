from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFade(Strategy):
    METADATA = {
        "name": "OvernightFadeETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold 4 hours, aiming for session midpoint
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown = 0
        self.last_signal_bar_index = -1
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.max_session_move_bps = 50 # Minimum move before considering a fade
        self.stalled_threshold_bars = 2 # How many bars price must not make new high/low
        self.fade_threshold_bps = 100 # How much price has moved from session open before fading

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown to prevent overtrading
        if self.entry_cooldown > 0:
            self.entry_cooldown -= 1
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Session boundaries: UTC 0-6 is the overnight session
        # We start tracking at 0 UTC
        if current_hour == 0 and self.session_start_bar_index != ctx.bar_index:
            # New session starts
            self.session_open_price = current_price
            self.session_high = current_price
            self.session_low = current_price
            self.session_start_bar_index = ctx.bar_index
            # Reset cooldown for new session
            self.entry_cooldown = 0
            return None # Don't trade on the first bar of the session

        # Update session high/low if within the session
        if self.session_open_price is not None and 0 <= current_hour < 6:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Logic to exit any open position before the next US session (e.g. 12 UTC)
        # Or before the overnight session ends completely (6 UTC)
        if ctx.has_position():
            if ctx.bar.timestamp.hour >= 6: # Exit before the session ends
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            # Also exit if price moves against us significantly from entry, acting as a dynamic stop
            if ctx.position_direction() == "long":
                if current_price < ctx.position["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000.0 * 0.75): # 75% of SL as dynamic exit
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "dynamic_stop_loss_long", "entry_price": ctx.position["entry_price"], "current_price": current_price})
            elif ctx.position_direction() == "short":
                if current_price > ctx.position["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000.0 * 0.75): # 75% of SL as dynamic exit
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "dynamic_stop_loss_short", "entry_price": ctx.position["entry_price"], "current_price": current_price})


        # Entry logic: Only trade between 2 and 5 UTC (allowing some session development)
        if not ctx.has_position() and 2 <= current_hour < 6 and self.session_open_price is not None:
            # Prevent re-entry on the same bar index
            if self.last_signal_bar_index == ctx.bar_index:
                return None

            # Regime filters
            if ctx.regime in ["volatile", "crisis"]:
                # print(f"Skipping trade due to regime: {ctx.regime}")
                return None
            
            # Avoid trading in strong trends (use a simple moving average)
            sma_period = 20 # Using 20-bar SMA for trend
            sma = self._sma(closes, sma_period)
            if sma is None:
                return None
            
            # If price is significantly above/below SMA, it might be a strong trend
            # We want to fade, so avoid strong trends
            sma_diff_bps = (current_price - sma) / sma * 10000
            if abs(sma_diff_bps) > 100: # If price is 1% away from SMA, consider it a trend
                # print(f"Skipping trade due to strong trend. SMA diff: {sma_diff_bps:.2f} bps")
                return None

            # Overnight session move calculation
            move_from_open_bps = (current_price - self.session_open_price) / self.session_open_price * 10000

            # Check for "stalled" condition
            # Has the price failed to make a new high/low for the last few bars?
            stalled_high = all(h < self.session_high for h in ctx.highs(self.stalled_threshold_bars))
            stalled_low = all(l > self.session_low for l in ctx.lows(self.stalled_threshold_bars))

            # Long entry: Fade a stretched move down
            if move_from_open_bps < -self.fade_threshold_bps and stalled_low:
                # Ensure the move is significant enough from the session low
                if (current_price - self.session_low) / self.session_low * 10000 < 20: # Must have bounced slightly from low
                    confidence = 0.6 + (min(abs(move_from_open_bps), 200) / 200 * 0.3) # More stretched = more confidence
                    self.entry_cooldown = 3 # Cooldown for 3 bars
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("long", confidence=confidence,
                                      metadata={
                                          "reason": "overnight_fade_long",
                                          "session_open_price": self.session_open_price,
                                          "current_price": current_price,
                                          "move_from_open_bps": move_from_open_bps,
                                          "stalled_low": True,
                                          "current_hour": current_hour,
                                          "sma_diff_bps": sma_diff_bps,
                                      })

            # Short entry: Fade a stretched move up
            elif move_from_open_bps > self.fade_threshold_bps and stalled_high:
                # Ensure the move is significant enough from the session high
                if (self.session_high - current_price) / self.session_high * 10000 < 20: # Must have pulled back slightly from high
                    confidence = 0.6 + (min(abs(move_from_open_bps), 200) / 200 * 0.3) # More stretched = more confidence
                    self.entry_cooldown = 3 # Cooldown for 3 bars
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("short", confidence=confidence,
                                      metadata={
                                          "reason": "overnight_fade_short",
                                          "session_open_price": self.session_open_price,
                                          "current_price": current_price,
                                          "move_from_open_bps": move_from_open_bps,
                                          "stalled_high": True,
                                          "current_hour": current_hour,
                                          "sma_diff_bps": sma_diff_bps,
                                      })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes, allowing a new trade in the next session or if conditions are met
        self.entry_cooldown = 0
        self.session_open_price = None # Reset session tracking after any closure
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1