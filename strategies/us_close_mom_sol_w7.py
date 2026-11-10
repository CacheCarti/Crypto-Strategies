from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionCloseMomentum(Strategy):
    METADATA = {
        "name": "Session Close Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 4  # Hours before session close
        self.volume_lookback = 20 # For median volume
        self.cooldown_bars = 6   # Cooldown after a trade

        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _median(self, data):
        if not data:
            return None
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        return sorted_data[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # US Session close is typically 17:00 ET, which is 21:00 UTC
        # We want to evaluate the 4 hours leading up to 21:00 UTC
        # So, we check if current bar is 21:00 UTC
        if ctx.bar.timestamp.hour == 21:
            # Need at least self.lookback_period bars
            if len(closes) < self.lookback_period + 1:
                return None

            # Calculate momentum over the last N hours (e.g., 17:00 UTC to 21:00 UTC)
            session_closes = closes[-self.lookback_period:]
            session_opens = opens[-self.lookback_period] # Open of the first bar in the lookback
            session_highs = highs[-self.lookback_period:]
            session_lows = lows[-self.lookback_period:]
            session_volumes = volumes[-self.lookback_period:]

            # Price change over the session
            price_change = session_closes[-1] - session_opens
            
            # Average range during the session
            avg_range = sum([h - l for h, l in zip(session_highs, session_lows)]) / self.lookback_period
            
            # Median range for comparison
            all_ranges = [h - l for h, l in zip(highs[-self.volume_lookback:], lows[-self.volume_lookback:])]
            median_range = self._median(all_ranges)
            if median_range is None or median_range == 0:
                median_range = 0.001 # Avoid division by zero

            # Average volume during the session
            avg_volume = sum(session_volumes) / self.lookback_period
            
            # Median volume for comparison
            median_volume = self._median(volumes[-self.volume_lookback:])
            if median_volume is None or median_volume == 0:
                median_volume = 0.001 # Avoid division by zero

            # Condition 1: Strong close (positive price change)
            strong_bullish_close = price_change > 0 and (price_change / session_opens) > 0.002 # 0.2% up
            strong_bearish_close = price_change < 0 and (abs(price_change) / session_opens) > 0.002 # 0.2% down

            # Condition 2: Above median range
            above_median_range = avg_range > median_range * 1.2 # 20% above median

            # Condition 3: Above median volume
            above_median_volume = avg_volume > median_volume * 1.2 # 20% above median

            signal_confidence = 0.65 # Base confidence

            # Long entry condition
            if strong_bullish_close and above_median_range and above_median_volume:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "us_session_close_bullish_momentum",
                            "price_change_pct": (price_change / session_opens) * 100,
                            "avg_range_ratio_median": avg_range / median_range,
                            "avg_volume_ratio_median": avg_volume / median_volume,
                            "close_hour": ctx.bar.timestamp.hour
                        }
                    )
            
            # Short entry condition
            elif strong_bearish_close and above_median_range and above_median_volume:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "us_session_close_bearish_momentum",
                            "price_change_pct": (price_change / session_opens) * 100,
                            "avg_range_ratio_median": avg_range / median_range,
                            "avg_volume_ratio_median": avg_volume / median_volume,
                            "close_hour": ctx.bar.timestamp.hour
                        }
                    )

        # Exit condition: If holding a position and it's 00:00 UTC (3 hours after entry)
        if ctx.has_position() and ctx.bar.timestamp.hour == 0:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "time_based_exit_utc_midnight",
                    "current_price": ctx.bar.close,
                    "entry_price": ctx.position["entry_price"],
                    "position_direction": ctx.position_direction()
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # This method is called when a position closes.
        # We already update last_trade_bar on entry, so no need to do it here for cooldown.
        pass