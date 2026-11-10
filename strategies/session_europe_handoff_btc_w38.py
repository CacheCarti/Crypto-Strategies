from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class BtcEuMorningImpulse(Strategy):
    METADATA = {
        "name": "BTC EU Morning Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 10   # UTC 10:00 (allowing entry up to and including 9:00 bar)
        self.exit_hour = 12        # UTC 12:00 (close position at 12:00 bar)
        self.min_bars_since_trade = 12 # Cooldown period (12 hours for 1h bars)
        self.last_trade_bar = -self.min_bars_since_trade

        # Parameters for impulse detection
        self.impulse_period = 3 # Look back 3 bars for impulse
        self.impulse_threshold_bps = 50 # 50 bps move to be considered an impulse

        # Indicator periods
        self.ema_short_period = 10
        self.ema_long_period = 30

    def _ema(self, values: collections.deque, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(list(values)[:period]) / period # Simple average for the initial EMA
        for v in list(values)[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Close position by self.exit_hour regardless
        if ctx.has_position() and current_hour >= self.exit_hour:
            # Only close if the position was opened today
            if ctx.position and ctx.position.get("metadata", {}).get("entry_day") == ctx.bar.timestamp.day:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "time_based_exit",
                    "exit_hour": self.exit_hour,
                    "current_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.min_bars_since_trade:
            return None

        # Entry logic: Only within defined EU morning hours
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Ensure we haven't traded yet today
            if self.last_trade_bar > 0 and ctx.bar.timestamp.day == ctx.bars[self.last_trade_bar].timestamp.day:
                return None # Already traded today

            # Calculate impulse
            if len(closes) < self.impulse_period + 1:
                return None

            # Calculate EMAs
            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)

            if ema_short is None or ema_long is None:
                return None

            # Impulse detection (price change over impulse_period bars)
            price_change_abs = abs(closes[-1] - closes[-self.impulse_period])
            price_change_bps = (price_change_abs / closes[-self.impulse_period]) * 10000

            if price_change_bps >= self.impulse_threshold_bps:
                # Directional impulse
                if closes[-1] > closes[-self.impulse_period]: # Upward impulse
                    if ema_short > ema_long: # Trend confirmation (bullish EMA cross)
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "eu_morning_long_impulse",
                            "impulse_bps": price_change_bps,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "price": ctx.bar.close,
                            "entry_day": ctx.bar.timestamp.day
                        })
                elif closes[-1] < closes[-self.impulse_period]: # Downward impulse
                    if ema_short < ema_long: # Trend confirmation (bearish EMA cross)
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "eu_morning_short_impulse",
                            "impulse_bps": price_change_bps,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "price": ctx.bar.close,
                            "entry_day": ctx.bar.timestamp.day
                        })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar if we want to allow re-entry on the same day after a quick close,
        # but the current logic prevents same-day re-entry.
        # This function is more for logging or adapting future trade sizes.
        pass