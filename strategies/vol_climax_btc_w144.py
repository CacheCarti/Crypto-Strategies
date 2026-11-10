from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.vol_std_multiplier = 2.0
        self.climax_range_multiplier = 1.5
        self.cooldown_bars = 12 # Wait 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)
        volumes = ctx.volumes(self.vol_period + 1)

        if len(closes) < self.vol_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate historical volatility (standard deviation of log returns)
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        volatility = self._stddev(log_returns, self.vol_period)
        if volatility is None:
            return None
        
        # Average volume for comparison
        avg_volume = sum(volumes[:-1]) / (len(volumes) - 1) if len(volumes) > 1 else 0

        # Current bar's true range and volume
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_volume = ctx.bar.volume

        # True Range calculation (approximate for current bar)
        prev_close = closes[-2]
        current_tr = max(current_high - current_low, abs(current_high - prev_close), abs(current_low - prev_close))

        # Check for volatility climax (high range and high volume) on the previous bar
        # We look at the PREVIOUS bar for the climax, and the current bar for the fade confirmation.
        prev_bar_high = highs[-2]
        prev_bar_low = lows[-2]
        prev_bar_close = closes[-2]
        prev_bar_open = opens[-2]
        prev_bar_volume = volumes[-2]
        
        prev_bar_tr = max(prev_bar_high - prev_bar_low, abs(prev_bar_high - closes[-3]), abs(prev_bar_low - closes[-3])) if len(closes) > 2 else 0

        # Define "exceptional" conditions
        # Volatility climax: previous bar's range is significantly higher than recent average AND high volume
        # Using a simple moving average of true range as a proxy for 'normal' range
        avg_tr = sum([max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes)-1)]) / (len(closes)-2) if len(closes) > 2 else 0

        is_vol_climax = (prev_bar_tr > avg_tr * self.climax_range_multiplier and 
                         prev_bar_volume > avg_volume * 1.5) # 50% above average volume

        if is_vol_climax:
            # Now check for fade condition on the current bar
            # A fade means the current bar fails to continue the previous bar's direction strongly.
            # Long setup: Previous bar was a strong down move (climax), current bar shows rebound/failure to continue down
            if prev_bar_close < prev_bar_open: # Previous bar was bearish
                if current_close > current_open and current_close > prev_bar_close: # Current bar is bullish and closes above previous close
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "bearish_climax_fade_rebound",
                            "prev_bar_tr_ratio": prev_bar_tr / avg_tr,
                            "prev_bar_volume_ratio": prev_bar_volume / avg_volume,
                            "current_close": current_close,
                            "prev_close": prev_bar_close,
                            "volatility": volatility
                        })
            
            # Short setup: Previous bar was a strong up move (climax), current bar shows weakness/failure to continue up
            elif prev_bar_close > prev_bar_open: # Previous bar was bullish
                if current_close < current_open and current_close < prev_bar_close: # Current bar is bearish and closes below previous close
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "bullish_climax_fade_rejection",
                            "prev_bar_tr_ratio": prev_bar_tr / avg_tr,
                            "prev_bar_volume_ratio": prev_bar_volume / avg_volume,
                            "current_close": current_close,
                            "prev_close": prev_bar_close,
                            "volatility": volatility
                        })
        
        # Exit logic (optional, but good for adaptive strategies)
        # If we have a position and the market regime shifts to crisis, consider flattening
        if ctx.has_position():
            if ctx.market.get("regime", "NORMAL") == "CRISIS" and ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "crisis_regime_exit_long", "regime": ctx.market.get("regime")})
            elif ctx.market.get("regime", "NORMAL") == "CRISIS" and ctx.position_direction() == "short":
                # For short positions, crisis might be beneficial, so don't exit unless it's a calm crisis
                if ctx.market.get("crisis_score", 0.0) < 0.5:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "crisis_regime_exit_short_low_score", "regime": ctx.market.get("regime")})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, even if it's not a new entry.
        # This prevents taking a new trade immediately after a stop-out.
        self.last_trade_bar = ctx.bar_index
        # Log closing details
        # ctx.log(f"Position closed: {position['direction']} at {position['exit_price']}. Return: {position['return_bps']} bps. Held for {position['bars_held']} bars.")