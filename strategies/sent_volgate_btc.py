from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateBTC(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.cooldown_bars = 10 # Cooldown after any trade (entry or exit)
        self.last_trade_bar = -self.cooldown_bars

        # Sentiment thresholds
        self.fear_threshold = 25 # Extreme Fear
        self.greed_threshold = 75 # Extreme Greed

        # Volatility thresholds (standard deviation of daily returns)
        self.vol_high_threshold = 0.015 # 1.5% daily vol
        self.vol_low_threshold = 0.005 # 0.5% daily vol

        # Price action confirmation (e.g., breakout from recent range)
        self.breakout_period = 10

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.vol_period + 1)
        if len(closes) < self.vol_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate daily returns for volatility
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = self._stddev(returns, self.vol_period)

        if volatility is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Price action confirmation: Check for recent highs/lows
        recent_highs = ctx.highs(self.breakout_period)
        recent_lows = ctx.lows(self.breakout_period)
        if len(recent_highs) < self.breakout_period or len(recent_lows) < self.breakout_period:
            return None

        highest_high = max(recent_highs)
        lowest_low = min(recent_lows)

        current_price = ctx.bar.close

        # Entry logic
        if not ctx.has_position():
            # Long entry: Extreme Fear + High Volatility + Price bouncing from recent lows
            if (fear_greed_index <= self.fear_threshold and
                volatility >= self.vol_high_threshold and
                current_price > lowest_low * 1.005 and # 0.5% bounce from low
                current_price < ctx.opens(1)[0]): # Current bar is up
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "extreme_fear_high_vol_bounce",
                        "fear_greed_index": fear_greed_index,
                        "volatility": volatility,
                        "current_price": current_price,
                        "lowest_low_breakout_period": lowest_low
                    }
                )

            # Short entry: Extreme Greed + High Volatility + Price breaking down from recent highs
            elif (fear_greed_index >= self.greed_threshold and
                  volatility >= self.vol_high_threshold and
                  current_price < highest_high * 0.995 and # 0.5% drop from high
                  current_price < ctx.opens(1)[0]): # Current bar is down
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "extreme_greed_high_vol_breakdown",
                        "fear_greed_index": fear_greed_index,
                        "volatility": volatility,
                        "current_price": current_price,
                        "highest_high_breakout_period": highest_high
                    }
                )
        else:
            # Exit logic (if sentiment becomes ambiguous or volatility drops)
            # This is a soft exit condition, not a hard stop/take-profit
            # The engine's SL/TP will handle hard exits
            if (fear_greed_index > self.fear_threshold + 5 and fear_greed_index < self.greed_threshold - 5) or \
               (volatility < self.vol_low_threshold):
                
                # Only exit if the position direction aligns with the sentiment shift
                if (ctx.position_direction() == "long" and fear_greed_index >= 50) or \
                   (ctx.position_direction() == "short" and fear_greed_index <= 50):
                    
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "sentiment_vol_ambiguous_exit",
                            "fear_greed_index": fear_greed_index,
                            "volatility": volatility,
                            "current_price": current_price
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive sizing here based on position["return_bps"]
        pass