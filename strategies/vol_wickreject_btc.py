from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityWickReject(Strategy):
    METADATA = {
        "name": "Volatility Wick Rejection BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 30,
        "required_features": [], # No external features used, only OHLCV and context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_wick_ratio = 0.6 # Minimum wick size relative to total bar range
        self.min_volume_mult = 1.5 # Minimum volume multiplier above average
        self.cooldown_bars = 12 # Cooldown for 12 bars (12 hours) after a trade
        self.last_signal_bar_index = -self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 2)
        opens = ctx.opens(self.METADATA["warmup_bars"] + 2)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 2)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 2)
        volumes = ctx.volumes(self.METADATA["warmup_bars"] + 2)

        if len(closes) < self.METADATA["warmup_bars"] + 2:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Calculate average volume over a recent period
        avg_volume_period = 20
        if len(volumes) < avg_volume_period + 2:
            return None
        recent_volumes = volumes[-avg_volume_period-2:-2] # Exclude current and previous bar
        if len(recent_volumes) == 0: # Handle case if not enough history for avg
            return None
        avg_volume = sum(recent_volumes) / len(recent_volumes)

        # Check for previous bar's rejection wick
        prev_open, prev_high, prev_low, prev_close, prev_volume = \
            opens[-2], highs[-2], lows[-2], closes[-2], volumes[-2]

        prev_bar_range = prev_high - prev_low
        if prev_bar_range == 0: # Avoid division by zero
            return None

        # Current bar price action
        current_open, current_high, current_low, current_close, current_volume = \
            opens[-1], highs[-1], lows[-1], closes[-1], volumes[-1]

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Bearish rejection wick followed by bullish confirmation
        if prev_close < prev_open: # Previous bar was bearish
            # Check for large upper wick
            upper_wick = prev_high - max(prev_open, prev_close)
            if upper_wick / prev_bar_range >= self.min_wick_ratio:
                # Check for high volume on rejection bar
                if prev_volume > avg_volume * self.min_volume_mult:
                    # Current bar confirms bullish reversal (closes above previous open)
                    if current_close > prev_open:
                        signal_direction = "long"
                        confidence = 0.75
                        reason = "bearish_wick_rejection_bullish_confirmation"
                        metadata = {
                            "prev_bar_upper_wick_ratio": upper_wick / prev_bar_range,
                            "prev_bar_volume_mult": prev_volume / avg_volume,
                            "prev_close": prev_close,
                            "prev_open": prev_open,
                            "current_close": current_close
                        }

        # Short signal: Bullish rejection wick followed by bearish confirmation
        elif prev_close > prev_open: # Previous bar was bullish
            # Check for large lower wick
            lower_wick = min(prev_open, prev_close) - prev_low
            if lower_wick / prev_bar_range >= self.min_wick_ratio:
                # Check for high volume on rejection bar
                if prev_volume > avg_volume * self.min_volume_mult:
                    # Current bar confirms bearish reversal (closes below previous open)
                    if current_close < prev_open:
                        signal_direction = "short"
                        confidence = 0.75
                        reason = "bullish_wick_rejection_bearish_confirmation"
                        metadata = {
                            "prev_bar_lower_wick_ratio": lower_wick / prev_bar_range,
                            "prev_bar_volume_mult": prev_volume / avg_volume,
                            "prev_close": prev_close,
                            "prev_open": prev_open,
                            "current_close": current_close
                        }

        if signal_direction and not ctx.has_position():
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )
        elif ctx.has_position() and ctx.position_direction() != signal_direction and signal_direction:
            # Flip position if new signal is opposite of current
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass