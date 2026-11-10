from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningImpulse(Strategy):
    METADATA = {
        "name": "EuropeanMorningImpulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1 hour hold, potentially longer for follow-through
        "warmup_bars": 50,              # Need enough bars for EMA and recent price history
        "required_features": [],        # No specific features beyond OHLCV needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar_index = -self.METADATA["warmup_bars"]
        self.last_entry_day = -1
        self.entry_price = 0.0
        self.entry_direction = None
        self.cooldown_bars = 4 # Cooldown for 4 bars after an exit

        # Parameters for impulse detection
        self.impulse_period = 4 # Bars for initial impulse detection (4 hours in 1h bars)
        self.ema_period = 20    # EMA for trend confirmation
        self.min_impulse_move_bps = 50 # Minimum price move in bps to consider it an impulse

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        opens = ctx.opens(self.impulse_period + 1)
        highs = ctx.highs(self.impulse_period + 1)
        lows = ctx.lows(self.impulse_period + 1)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- Exit Logic (if holding a position) ---
        if ctx.has_position():
            # Mandatory exit by UTC 12:00
            if current_hour >= 12 and ctx.position_direction() == self.entry_direction:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "eod_session_close",
                        "entry_price": self.entry_price,
                        "current_price": ctx.bar.close,
                        "bars_held": ctx.bar_index - self.last_signal_bar_index,
                        "exit_hour": current_hour
                    }
                )
            return None # Hold position if not exit time

        # --- Cooldown Logic ---
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # --- Entry Logic (only between UTC 7:00 and 11:00, and only once per day) ---
        if current_hour >= 7 and current_hour < 11 and current_day != self.last_entry_day:
            
            # Check for sufficient data for impulse detection
            if len(opens) < self.impulse_period + 1 or len(closes) < self.impulse_period + 1:
                return None
            
            # Calculate impulse over the last 'impulse_period' bars
            # Start of the impulse period (e.g., at 7 AM, look at 7-10 AM candles)
            impulse_start_open = opens[-(self.impulse_period + 1)] 
            impulse_end_close = closes[-1]
            
            price_change_bps = ((impulse_end_close - impulse_start_open) / impulse_start_open) * 10000

            ema_val = self._ema(closes[:-1], self.ema_period) # EMA from previous bar
            if ema_val is None:
                return None

            signal_direction = None
            reason = ""
            
            # Long impulse
            if price_change_bps > self.min_impulse_move_bps:
                # Confirm with price above EMA
                if ctx.bar.close > ema_val:
                    signal_direction = "long"
                    reason = "european_morning_long_impulse_above_ema"
            # Short impulse
            elif price_change_bps < -self.min_impulse_move_bps:
                # Confirm with price below EMA
                if ctx.bar.close < ema_val:
                    signal_direction = "short"
                    reason = "european_morning_short_impulse_below_ema"

            if signal_direction:
                self.last_signal_bar_index = ctx.bar_index
                self.last_entry_day = current_day
                self.entry_price = ctx.bar.close
                self.entry_direction = signal_direction
                
                return ctx.signal(
                    signal_direction,
                    confidence=0.75, # High confidence for strong impulse + trend confirmation
                    metadata={
                        "reason": reason,
                        "impulse_bps": round(price_change_bps, 2),
                        "current_close": ctx.bar.close,
                        "ema_val": round(ema_val, 2),
                        "hour": current_hour
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry tracking after a position closes
        self.entry_direction = None
        self.entry_price = 0.0
        # Cooldown will be handled by `self.last_signal_bar_index` which is set on signal creation.