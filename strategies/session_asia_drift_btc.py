from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftBTC(Strategy):
    METADATA = {
        "name": "AsiaDriftBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours approx, to catch the drift
        "warmup_bars": 30,
        "required_features": [], # No external features needed for this price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.asia_session_start_hour = 0
        self.asia_session_end_hour = 8
        self.range_period = 20 # For median range calculation
        self.min_move_multiplier = 1.0 # Minimum move compared to median range
        self.max_move_multiplier = 2.5 # Max move before considering it exhausted
        self.min_body_ratio = 0.6 # Minimum body size relative to total bar range
        self.max_wick_ratio = 0.3 # Max wick size relative to total bar range
        self.min_volume_ratio = 1.2 # Volume must be above 1.2x its 10-bar SMA

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _median(self, values):
        if not values:
            return None
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) <= self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic to exit before the end of the Asia session if still holding from a previous day
        if ctx.has_position():
            if current_hour >= self.asia_session_end_hour:
                # If we entered a long, and it's past the Asia session end, exit.
                # The strategy is about capturing the drift *into* the next session,
                # so if we're still holding at the start of a new Asia session, something is off.
                # Or if we're simply past the intended hold time.
                if ctx.position["direction"] == "long" and ctx.bar_index - self.last_trade_bar >= self.declared_hold_seconds / (60 * 60):
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_hold_time_exceeded", "current_hour": current_hour})


        # Entry logic: Only consider entries at the start of a new session (e.g., just after Asia session ends)
        # We want to catch the drift *after* the Asia session, so we evaluate the Asia bar at its close (UTC 7:00)
        # and trigger at UTC 8:00 for the next bar.
        if current_hour == self.asia_session_end_hour: # This bar's data represents the end of the Asia session
            # We need the last bar of the Asia session (UTC 7:00 bar, which closes at UTC 8:00)
            # This is the current bar in ctx.bar
            
            # Calculate daily range for context
            if len(highs) < self.range_period or len(lows) < self.range_period:
                return None
            
            # Calculate the median true range for the last 'range_period' bars
            true_ranges = []
            for i in range(1, len(closes)):
                tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                true_ranges.append(tr)
            
            median_range = self._median(true_ranges[-self.range_period:])
            if median_range is None or median_range == 0:
                return None

            # Analyze the 'Asia session' bar (the bar that just closed at self.asia_session_end_hour)
            session_bar_open = opens[-1]
            session_bar_close = closes[-1]
            session_bar_high = highs[-1]
            session_bar_low = lows[-1]
            session_bar_volume = volumes[-1]

            bar_range = session_bar_high - session_bar_low
            bar_body = abs(session_bar_close - session_bar_open)

            # Check for decisive move (strong body, not too much wick)
            if bar_range == 0: # Avoid division by zero
                return None

            body_ratio = bar_body / bar_range
            upper_wick = session_bar_high - max(session_bar_open, session_bar_close)
            lower_wick = min(session_bar_open, session_bar_close) - session_bar_low
            
            # Check for single bar spike (large wicks)
            if upper_wick / bar_range > self.max_wick_ratio or lower_wick / bar_range > self.max_wick_ratio:
                return None

            # Check if move is decisive but not exhausted
            move_size = abs(session_bar_close - session_bar_open)
            if not (self.min_move_multiplier * median_range <= move_size <= self.max_move_multiplier * median_range):
                return None

            # Check volume
            volume_sma = self._sma(volumes, 10)
            if volume_sma is None or session_bar_volume < volume_sma * self.min_volume_ratio:
                return None

            # Determine direction of the decisive move
            if session_bar_close > session_bar_open and body_ratio >= self.min_body_ratio: # Bullish Asia bar
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_bull_drift_entry",
                            "session_bar_close": session_bar_close,
                            "session_bar_open": session_bar_open,
                            "session_bar_range": bar_range,
                            "median_range": median_range,
                            "volume": session_bar_volume,
                            "volume_sma10": volume_sma,
                            "body_ratio": body_ratio
                        }
                    )
            elif session_bar_close < session_bar_open and body_ratio >= self.min_body_ratio: # Bearish Asia bar
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_bear_drift_entry",
                            "session_bar_close": session_bar_close,
                            "session_bar_open": session_bar_open,
                            "session_bar_range": bar_range,
                            "median_range": median_range,
                            "volume": session_bar_volume,
                            "volume_sma10": volume_sma,
                            "body_ratio": body_ratio
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass