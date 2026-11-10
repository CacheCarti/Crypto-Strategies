from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolConfirmation(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.std_dev_multiplier = 1.5
        self.fg_fear_threshold = 25
        self.fg_greed_threshold = 75
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period * 2) # Need enough for volatility calc
        highs = ctx.highs(self.vol_period * 2)
        lows = ctx.lows(self.vol_period * 2)

        if len(closes) < self.vol_period * 2:
            return None

        # Cooldown period after a trade
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate historical volatility (e.g., using standard deviation of returns)
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        if len(returns) < self.vol_period:
            return None
        
        current_vol = self._stddev(returns[-self.vol_period:], self.vol_period)
        
        # Calculate recent average volatility for comparison
        # Using a slightly longer period for the average to detect relative spikes
        avg_vol_period = self.vol_period * 2
        if len(returns) < avg_vol_period:
            return None
        
        avg_vol = self._stddev(returns[-avg_vol_period:-self.vol_period], self.vol_period) # Vol before current window
        
        if current_vol is None or avg_vol is None:
            return None

        # Determine if volatility is significantly higher than average
        vol_spike = current_vol > avg_vol * self.std_dev_multiplier
        
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        signal_confidence = 0.0
        signal_direction = "hold"
        signal_reason = ""
        
        # Long condition: Extreme Fear + Volatility Spike
        if fear_greed_index <= self.fg_fear_threshold and vol_spike and not ctx.has_position():
            signal_direction = "long"
            signal_confidence = 0.75
            signal_reason = "extreme_fear_vol_spike_long"

        # Short condition: Extreme Greed + Volatility Spike
        elif fear_greed_index >= self.fg_greed_threshold and vol_spike and not ctx.has_position():
            signal_direction = "short"
            signal_confidence = 0.75
            signal_reason = "extreme_greed_vol_spike_short"
            
        # Exit logic: if sentiment neutralizes or volatility subsides significantly
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # If sentiment moves away from extreme or vol subsides, consider exiting
            if pos_dir == "long" and fear_greed_index > self.fg_fear_threshold + 10: # Fear subsides
                if not vol_spike and current_vol < avg_vol * 1.2: # Volatility too low
                    signal_direction = "flat"
                    signal_confidence = 0.5
                    signal_reason = "long_exit_fear_subsiding_vol_calm"
            elif pos_dir == "short" and fear_greed_index < self.fg_greed_threshold - 10: # Greed subsides
                if not vol_spike and current_vol < avg_vol * 1.2: # Volatility too low
                    signal_direction = "flat"
                    signal_confidence = 0.5
                    signal_reason = "short_exit_greed_subsiding_vol_calm"

        if signal_direction != "hold":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                metadata={
                    "reason": signal_reason,
                    "fear_greed_index": fear_greed_index,
                    "current_volatility": current_vol,
                    "average_volatility": avg_vol,
                    "volatility_spike": vol_spike,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, potentially adjust parameters for adaptive logic
        pass