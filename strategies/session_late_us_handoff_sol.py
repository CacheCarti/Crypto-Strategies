from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionHandoff(Strategy):
    METADATA = {
        "name": "SOL Late US Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 30,  # Need enough bars for 20-period indicators
        "required_features": [], # No external features used for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 8
        self.ema_period_slow = 20
        self.min_volume_multiplier = 1.2
        self.last_trade_bar = -self.warmup_bars # Initialize to allow trading after warmup
        self.cooldown_bars = 4 # Cooldown for 4 bars after a trade
        self.entry_hour_start = 18 # UTC 18 (6 PM UTC)
        self.entry_hour_end = 23 # UTC 23 (11 PM UTC)
        self.exit_hour = 0 # UTC 0 (midnight UTC)

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the first 'period' values
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate EMAs
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        sma_volume = self._sma(volumes, self.ema_period_slow) # Use same period for volume SMA

        if ema_fast is None or ema_slow is None or sma_volume is None:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Exit logic for existing position
        if ctx.has_position():
            # Mandatory exit at self.exit_hour (midnight UTC)
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": self.exit_hour,
                    "current_price": ctx.bar.close
                })
            return None # Hold position if not exiting

        # Entry logic: Only consider entries during late US session handoff (UTC 18-23)
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            return None

        # Calculate session directional impulse (simple EMA cross in the session window)
        # Check for a fresh cross within the session window
        prev_ema_fast = self._ema(closes[:-1], self.ema_period_fast)
        prev_ema_slow = self._ema(closes[:-1], self.ema_period_slow)

        if prev_ema_fast is None or prev_ema_slow is None:
            return None

        # Volume confirmation
        is_high_volume = ctx.bar.volume > (sma_volume * self.min_volume_multiplier)

        # Long entry conditions
        long_condition = (
            ema_fast > ema_slow and
            prev_ema_fast <= prev_ema_slow and # Fresh bullish cross
            ctx.bar.close > ema_slow and # Price above slow EMA
            is_high_volume # Volume confirmation
        )

        # Short entry conditions
        short_condition = (
            ema_fast < ema_slow and
            prev_ema_fast >= prev_ema_slow and # Fresh bearish cross
            ctx.bar.close < ema_slow and # Price below slow EMA
            is_high_volume # Volume confirmation
        )

        if long_condition:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "session_late_us_handoff_long",
                "entry_hour": current_hour,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "current_price": ctx.bar.close,
                "volume_ratio": ctx.bar.volume / sma_volume
            })
        elif short_condition:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "session_late_us_handoff_short",
                "entry_hour": current_hour,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "current_price": ctx.bar.close,
                "volume_ratio": ctx.bar.volume / sma_volume
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position details or adapt strategy based on performance
        # For this strategy, we mainly use last_trade_bar for cooldown
        pass