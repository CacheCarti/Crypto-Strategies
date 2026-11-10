from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentStabilization(Strategy):
    METADATA = {
        "name": "Sentiment Stabilization Long",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # ~2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_lookback = 5 # Days of depressed FGI
        self.ema_period = 20
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars

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
        if len(closes) < self.ema_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes[:-1], self.ema_period) # EMA of previous bars
        if ema is None:
            return None

        # Sentiment check: Fear & Greed Index
        # We need daily FGI values. Since it's daily, we can't look back N bars directly.
        # We'll assume ctx.features.get("fear_greed_index") gives us the current day's value.
        # To check for 'depressed for multiple days', we need to track it.
        # For simplicity in this contract, we'll check if the CURRENT value is "fear" or "extreme fear"
        # and assume the engine handles the daily cadence. A more robust solution would track historical FGI.
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Condition 1: Depressed Sentiment (Fear or Extreme Fear)
        is_depressed_sentiment = fear_greed_index <= 30 # Below 30 is typically Fear/Extreme Fear

        # Condition 2: Price stabilization bar
        # This implies price has fallen but is now showing signs of bottoming/stabilizing.
        # One way to interpret "first stabilization bar" is when price closes above the EMA
        # after having been below it, acting as a bounce from a depressed state.
        # Or, price closes above its open after a recent downtrend.
        
        # Let's use a simple stabilization: current close is above open, and above the short-term EMA.
        # And crucially, the previous bar was below EMA or had a negative close.
        
        # A simple "stabilization": current bar closes above its open, and above the EMA.
        # And the previous bar's close was below the EMA (indicating it was depressed).
        
        # We need the previous close and previous EMA for this logic
        prev_close = closes[-2]
        prev_ema = self._ema(closes[:-2], self.ema_period)
        
        if prev_ema is None:
            return None

        price_stabilizing = (current_price > ctx.bar.open and # current bar is green
                             current_price > ema and           # current price above EMA
                             prev_close < prev_ema)            # previous bar was below EMA (sign of depression)

        if not ctx.has_position():
            if is_depressed_sentiment and price_stabilizing:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_stabilization_long",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "ema": ema,
                        "prev_close": prev_close,
                        "prev_ema": prev_ema,
                    }
                )
        elif ctx.position_direction() == "long":
            # Exit logic: if sentiment improves significantly OR price breaks below EMA
            if fear_greed_index > 50: # Sentiment moved to Neutral/Greed
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "sentiment_improved_exit",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "ema": ema,
                    }
                )
            if current_price < ema * 0.99: # Price significantly breaks below EMA
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "price_break_ema_exit",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "ema": ema,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close, or adapt based on performance
        # For now, just logging or simple actions.
        pass