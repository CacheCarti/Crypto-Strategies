from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class OvernightImpulseFollowThrough(Strategy):
    METADATA = {
        "name": "Overnight Impulse Follow Through",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold 4 hours, but often less
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 6 # overnight session length in hours
        self.entry_hour_start = 6 # UTC 6 AM
        self.entry_hour_end = 10 # UTC 10 AM
        self.exit_hour = 14 # UTC 2 PM (before US session fully kicks in)
        self.min_impulse_vol_ratio = 1.2 # volume during impulse must be 20% higher than avg
        self.min_impulse_move_pct = 0.5 # 0.5% move in 6 hours
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For confidence scaling
        self.fear_greed_threshold_long = 40 # Fear or Extreme Fear
        self.fear_greed_threshold_short = 60 # Greed or Extreme Greed

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_handoff_exit", "exit_hour": self.exit_hour})
            return None # Hold position if not exit hour

        # Entry logic - only consider entries within the entry window and after cooldown
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for overnight impulse (previous 6 bars, assuming 1h bars)
        overnight_closes = ctx.closes(self.lookback_period + 1)
        overnight_volumes = ctx.volumes(self.lookback_period)

        if len(overnight_closes) < self.lookback_period + 1 or len(overnight_volumes) < self.lookback_period:
            return None

        overnight_start_price = overnight_closes[0]
        overnight_end_price = overnight_closes[-1]
        overnight_price_change_pct = ((overnight_end_price - overnight_start_price) / overnight_start_price) * 100

        avg_volume_overnight = sum(overnight_volumes) / self.lookback_period
        avg_volume_long_term = self._sma(volumes, 48) # 2 days avg volume
        if avg_volume_long_term is None:
            return None
        
        volume_ratio = avg_volume_overnight / avg_volume_long_term

        signal_direction = None
        reason = "no_signal"
        confidence = 0.0

        # Long Impulse
        if overnight_price_change_pct > self.min_impulse_move_pct and volume_ratio > self.min_impulse_vol_ratio:
            signal_direction = "long"
            reason = "overnight_bullish_impulse_follow_through"
            confidence = 0.6

        # Short Impulse
        elif overnight_price_change_pct < -self.min_impulse_move_pct and volume_ratio > self.min_impulse_vol_ratio:
            signal_direction = "short"
            reason = "overnight_bearish_impulse_follow_through"
            confidence = 0.6

        if signal_direction:
            # Gating with Fear & Greed Index for confidence scaling
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            
            if signal_direction == "long" and fear_greed_index <= self.fear_greed_threshold_long:
                confidence += 0.2 # Boost confidence if fearful market for long
            elif signal_direction == "short" and fear_greed_index >= self.fear_greed_threshold_short:
                confidence += 0.2 # Boost confidence if greedy market for short
            else:
                confidence -= 0.1 # Slight penalty if F&G not aligned

            confidence = min(1.0, max(0.0, confidence)) # Clamp confidence

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "overnight_price_change_pct": overnight_price_change_pct,
                    "overnight_volume_ratio": volume_ratio,
                    "fear_greed_index": fear_greed_index,
                    "current_hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position close
        pass