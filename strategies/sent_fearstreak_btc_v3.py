from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentStabilizationStrategy(Strategy):
    METADATA = {
        "name": "Sentiment Stabilization Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 5 # Look back 5 daily readings for fear
        self.ema_period = 20 # EMA for price stabilization
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State variables for detecting transitions
        self.prev_fear_greed_index = 50.0
        self.prev_ema_cross_signal = False


    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        
        # 1. Fear & Greed Index Check (slow state variable)
        # Fear & Greed is daily, so we need to track its changes
        current_fear_greed = ctx.features.get("fear_greed_index", 50.0)
        
        # Check for depressed sentiment over a period
        fear_streak = 0
        # Iterate backwards through the last `fear_greed_period` bars
        # Note: fear_greed_index is a daily feature, so it will be the same for multiple hourly bars
        # We need to detect when the *daily* value actually changes.
        
        # To handle daily cadence on hourly bars, we approximate by checking if the value has been
        # consistently below a threshold for a sufficient number of *unique daily readings*.
        # For simplicity, we'll check if the *current* reading is depressed and assume it has been
        # for a while if it's below the threshold. A more robust solution would track daily changes.
        
        # For now, let's simplify: if the current F&G is below 30 (Fear/Extreme Fear)
        # and has stayed there for a few checks (approximating multiple days).
        
        # A simple approach for detecting a "streak" in a daily-cadence feature on hourly bars:
        # Check if the current value is low and if the value N hours ago was also low.
        # This implicitly handles the daily update.
        
        # Let's say "depressed" means F&G < 35
        is_depressed_sentiment = False
        if current_fear_greed < 35:
            # Check if the F&G was also depressed some hours ago to simulate a "streak"
            # Since F&G is daily, we can check a value from, say, 24 hours ago (24 bars for 1h TF)
            # This is a proxy for "multiple daily readings"
            past_fear_greed_24h = ctx.features.get("fear_greed_index", 50.0, lookback_bars=24)
            if past_fear_greed_24h < 35:
                is_depressed_sentiment = True

        # 2. Price Stabilization (OHLCV trigger)
        # Use EMA cross as a stabilization signal
        ema_values = [self._ema(closes[:i+1], self.ema_period) for i in range(len(closes) - self.ema_period + 1, len(closes))]
        current_ema = self._ema(closes, self.ema_period)

        if current_ema is None:
            return None

        # Look for a stabilization signal: price crossing above EMA
        # This needs to be a transition, not a continuous state
        price_above_ema = current_price > current_ema
        
        # Detect a cross from below to above
        # To do this, we need the previous bar's closing price relative to its EMA
        # We need at least two bars for this
        if len(closes) < self.ema_period + 1:
            return None
        
        prev_closes = ctx.closes(2)
        prev_price = prev_closes[0]
        
        # Calculate EMA for the previous bar state
        prev_ema_values = [self._ema(closes[:-1][:i+1], self.ema_period) for i in range(len(closes[:-1]) - self.ema_period + 1, len(closes[:-1]))]
        prev_ema = None
        if prev_ema_values:
            prev_ema = prev_ema_values[-1]

        price_stabilization_trigger = False
        if prev_ema is not None and prev_price <= prev_ema and current_price > current_ema:
            price_stabilization_trigger = True

        # Combine conditions for a long signal
        if is_depressed_sentiment and price_stabilization_trigger:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75, # High confidence for this specific setup
                metadata={
                    "reason": "sentiment_stabilization_long",
                    "fear_greed_index": current_fear_greed,
                    "current_price": current_price,
                    "ema_period": self.ema_period,
                    "current_ema": current_ema,
                    "prev_price_was_below_ema": prev_price <= prev_ema,
                }
            )

        # We do not explicitly look for shorts with this strategy, it's designed for long entries
        # based on fear sentiment. If we have a position and conditions turn unfavorable,
        # we might want to exit, but the engine's SL/TP will handle most of that.
        # For a flat signal, we'd need a separate exit condition.
        # For this strategy, we'll rely on SL/TP for exits.

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, adapt strategy based on position outcome
        # For now, just reset the cooldown.
        # self.last_trade_bar is already updated on signal.
        pass