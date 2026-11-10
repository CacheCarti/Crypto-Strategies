from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralPullback(Strategy):
    METADATA = {
        "name": "Structural Pullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours average hold
        "warmup_bars": 60, # Need enough bars for 50-period indicators
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 50
        self.pullback_period = 14
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_high_price = 0.0
        self.last_low_price = 0.0
        self.in_pullback = False
        self.pullback_start_bar = -1

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
        closes = ctx.closes(self.trend_period + self.pullback_period + 5) # Ensure enough data for all periods
        highs = ctx.highs(self.trend_period + self.pullback_period + 5)
        lows = ctx.lows(self.trend_period + self.pullback_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score of price relative to its trend_period average
        price_zscore = self._zscore(closes, self.trend_period)
        if price_zscore is None:
            return None

        # Determine trend using a longer SMA
        long_sma = self._sma(closes, self.trend_period)
        if long_sma is None:
            return None

        # Check for higher highs and higher lows for an uptrend
        # This is a simplified check, a more robust one would involve pivot points
        is_uptrend = False
        if len(highs) > self.trend_period and len(lows) > self.trend_period:
            # Check if current price is above the long SMA
            if current_price > long_sma:
                # Look for a sequence of higher highs and higher lows
                # Simplified: compare current price to previous high/lows over a shorter period
                recent_high = max(highs[-self.pullback_period:])
                recent_low = min(lows[-self.pullback_period:])

                # Check if the market is making higher highs and higher lows in the recent past
                # This is a heuristic and not a strict definition of market structure
                if current_price > self.last_high_price and ctx.bar.low > self.last_low_price:
                    is_uptrend = True
                
                self.last_high_price = recent_high
                self.last_low_price = recent_low

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Buy on the first pullback after a confirmed uptrend, when price bounces from a lower Z-score
        if not ctx.has_position() and is_uptrend:
            # Look for a pullback: price significantly below recent highs but still above long-term trend
            # Use Z-score to detect price being "cheap" relative to its recent average
            if price_zscore < -1.0 and not self.in_pullback: # Price is significantly below its mean, starting pullback
                self.in_pullback = True
                self.pullback_start_bar = ctx.bar_index
                
            if self.in_pullback and ctx.bar_index > self.pullback_start_bar:
                # Check for signs of bounce from pullback: current price turning up from low Z-score
                # For simplicity, we look for a slight increase after a low Z-score, indicating a turn
                if current_price > closes[-2] and price_zscore > -0.5: # Price turned up and Z-score improved
                    # Ensure it's the 'first' meaningful pullback by checking if we had a strong move up before this pullback
                    # Simplified: ensure we are not in a sustained downtrend within the pullback period
                    if self._sma(closes, self.pullback_period) > self._sma(closes, self.pullback_period - 5): # Short-term SMA is rising
                        self.last_trade_bar = ctx.bar_index
                        self.in_pullback = False # Reset for next pullback
                        
                        # Use market context for confidence scaling
                        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
                        confidence = min(0.6 + trend_confidence * 0.3, 0.9) # Scale confidence with market trend strength

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            metadata={
                                "reason": "structural_pullback_entry",
                                "price_zscore": price_zscore,
                                "current_price": current_price,
                                "long_sma": long_sma,
                                "trend_confidence": trend_confidence,
                            }
                        )

        # Exit logic: Structure breaks (price closes below long SMA, or significant drop)
        # Or if we reach a prior high (take profit)
        if ctx.has_position() and ctx.position_direction() == "long":
            position_entry_price = ctx.position["entry_price"]
            
            # Stop loss: If price falls significantly below long SMA
            if current_price < long_sma * 0.98: # 2% below long SMA
                self.last_trade_bar = ctx.bar_index
                self.in_pullback = False # Reset
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "structural_break_stop_loss",
                        "current_price": current_price,
                        "long_sma": long_sma,
                        "price_zscore": price_zscore,
                    }
                )
            
            # Take profit: If price approaches a recent high (simplified, could be a previous pivot high)
            # Or if it has moved significantly in profit
            profit_bps = ((current_price - position_entry_price) / position_entry_price) * 10000
            if profit_bps >= self.METADATA["declared_tp_bps"] * 0.75: # Exit at 75% of declared TP
                self.last_trade_bar = ctx.bar_index
                self.in_pullback = False # Reset
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "prior_high_profit_target_hit",
                        "current_price": current_price,
                        "entry_price": position_entry_price,
                        "profit_bps": profit_bps,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state variables that might influence the next trade after a position closes
        self.in_pullback = False 
        # The last_trade_bar is already updated in on_bar for signals, but good to ensure
        # self.last_trade_bar = ctx.bar_index # This is handled by the signal itself
        pass