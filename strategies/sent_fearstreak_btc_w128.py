from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStabilization(Strategy):
    METADATA = {
        "name": "FearGreedStabilization",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # Aim for 2-hour holds
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 5  # How many bars to look back for depressed F&G
        self.stabilization_period = 3 # Bars for price stabilization
        self.cooldown_bars = 5      # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.fear_greed_period + self.stabilization_period + 2)
        highs = ctx.highs(self.fear_greed_period + self.stabilization_period + 2)
        lows = ctx.lows(self.fear_greed_period + self.stabilization_period + 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Fear & Greed Index as a slow state variable
        # We need daily F&G values. On 1h bars, F&G is updated once every 24 bars.
        # Check F&G for the last N distinct daily readings.
        fear_greed_values = []
        last_fg_day = -1
        for i in range(len(closes) - 1, -1, -1):
            bar_timestamp = ctx.bar.timestamp - (ctx.bar_index - i) * ctx.bar.resolution_seconds
            current_fg_day = bar_timestamp.day
            if current_fg_day != last_fg_day:
                fg_val = ctx.features.get("fear_greed_index", 50.0) # F&G is constant for the day
                fear_greed_values.append(fg_val)
                last_fg_day = current_fg_day
            if len(fear_greed_values) >= self.fear_greed_period:
                break
        
        if len(fear_greed_values) < self.fear_greed_period:
            return None

        # Condition 1: Depressed Fear & Greed Index
        # All recent F&G readings must be below a threshold (e.g., 30 for "Fear" or "Extreme Fear")
        is_fearful_regime = all(fg <= 30 for fg in fear_greed_values)

        if not is_fearful_regime:
            return None

        # Condition 2: Price stabilization after potential dip
        # We look for a few bars where price shows signs of bottoming/stabilizing.
        # This could be current close > previous close for N bars, or current close > SMA of recent lows.
        
        # Simple stabilization: current close is above the average of the last few lows
        # and has closed higher than the previous bar for at least one bar.
        
        recent_lows = lows[-self.stabilization_period:]
        if len(recent_lows) < self.stabilization_period:
            return None
        
        avg_recent_low = sum(recent_lows) / self.stabilization_period

        # Additional check: current close is above its short-term SMA to confirm upward momentum
        short_sma_period = 3
        if len(closes) < short_sma_period:
            return None
        short_sma = self._sma(closes, short_sma_period)
        if short_sma is None:
            return None

        price_stabilizing = (ctx.bar.close > avg_recent_low) and \
                            (ctx.bar.close > closes[-2]) and \
                            (ctx.bar.close > short_sma)
        
        if not price_stabilizing:
            return None

        # Entry Signal: Long only
        if not ctx.has_position():
            confidence = 0.6 + (ctx.rng.uniform(0, 1) * 0.2) # Base confidence, add some noise
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "fear_greed_depressed_price_stabilization",
                    "fear_greed_index_recent_avg": sum(fear_greed_values) / len(fear_greed_values),
                    "current_close": ctx.bar.close,
                    "avg_recent_low": avg_recent_low,
                    "short_sma": short_sma
                }
            )
        
        # If in position, hold
        return ctx.signal("hold")

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing details or adapt strategy if needed
        pass