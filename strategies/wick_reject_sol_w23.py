from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RejectionWickReversal(Strategy):
    METADATA = {
        "name": "Rejection Wick Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 30,
        "required_features": []
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_ratio_threshold = 0.6  # Wick must be at least 60% of the total bar range
        self.volume_ma_period = 20
        self.volume_multiplier = 1.5   # Volume must be 1.5x average
        self.confirmation_bars = 1      # Look for confirmation on the next bar
        self.cooldown_bars = 5          # Cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars

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

        if len(closes) < self.warmup_bars + self.confirmation_bars + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
            return None
            
        # Check for existing position to manage exits
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            current_price = ctx.bar.close

            # Partial wick retracement target (example, could be refined)
            # For simplicity, let's say we target 50% of the wick size that triggered the entry
            # This would require storing the wick size in metadata, which is good practice.
            # For now, let's use a simpler time-based or profit-based exit.
            
            # If we've held for a while and are in profit, consider taking profit
            if position["direction"] == "long" and current_price > entry_price * (1 + self.METADATA["declared_tp_bps"] / 10000.0):
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "take_profit_target_reached", "entry_price": entry_price, "current_price": current_price})
            if position["direction"] == "short" and current_price < entry_price * (1 - self.METADATA["declared_tp_bps"] / 10000.0):
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "take_profit_target_reached", "entry_price": entry_price, "current_price": current_price})

            # If the market regime turns highly volatile or crisis, consider exiting
            if ctx.market.get("regime") in ["HIGH_VOL", "CRISIS"]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "regime_change_exit", "current_regime": ctx.market.get("regime")})

            return None # Hold position if no exit condition met

        # Calculate average volume
        avg_volume = self._sma(volumes[:-self.confirmation_bars], self.volume_ma_period)
        if avg_volume is None:
            return None

        # Analyze the potential rejection bar (bar before current)
        rejection_bar_idx = -self.confirmation_bars - 1
        rejection_open = opens[rejection_bar_idx]
        rejection_close = closes[rejection_bar_idx]
        rejection_high = highs[rejection_bar_idx]
        rejection_low = lows[rejection_bar_idx]
        rejection_volume = volumes[rejection_bar_idx]

        bar_range = rejection_high - rejection_low
        if bar_range == 0: # Avoid division by zero
            return None

        # Check for high volume
        if rejection_volume < avg_volume * self.volume_multiplier:
            return None

        signal_direction = None
        wick_type = None

        # Check for large upper wick (bearish rejection)
        upper_wick = rejection_high - max(rejection_open, rejection_close)
        if upper_wick / bar_range >= self.wick_ratio_threshold and rejection_close < rejection_open: # Ensure it's a bearish candle
            # Check confirmation on the current bar: current bar closes lower than rejection bar's close
            if ctx.bar.close < rejection_close:
                signal_direction = "short"
                wick_type = "upper"

        # Check for large lower wick (bullish rejection)
        lower_wick = min(rejection_open, rejection_close) - rejection_low
        if lower_wick / bar_range >= self.wick_ratio_threshold and rejection_close > rejection_open: # Ensure it's a bullish candle
            # Check confirmation on the current bar: current bar closes higher than rejection bar's close
            if ctx.bar.close > rejection_close:
                signal_direction = "long"
                wick_type = "lower"

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=0.7, # Moderate confidence
                metadata={
                    "reason": f"rejection_wick_reversal_{wick_type}_confirmed",
                    "rejection_bar_close": rejection_close,
                    "rejection_bar_volume": rejection_volume,
                    "avg_volume": avg_volume,
                    "wick_ratio": upper_wick / bar_range if wick_type == "upper" else lower_wick / bar_range,
                    "current_close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close or adapt strategy based on performance
        pass