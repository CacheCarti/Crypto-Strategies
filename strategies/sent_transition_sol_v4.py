from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentRecovery(Strategy):
    METADATA = {
        "name": "Solana Sentiment Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 7 # Lookback for fear_greed_index
        self.ema_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate first trade

        # Store previous fear_greed_index to detect transitions
        self.prev_fear_greed_index = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # Calculate EMA and RSI
        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_val is None or rsi_val is None:
            return None

        # Sentiment Recovery Logic
        # Look for a transition from Extreme Fear (0-20) or Fear (21-40) to a recovering state (above 20 or 40)
        # And ensure the current fear_greed_index is not too high (avoid extreme greed)
        
        # Get historical fear_greed_index values
        # Since fear_greed_index is daily, we'll check the last few distinct values
        fear_greed_history = []
        for i in range(max(0, ctx.bar_index - self.fear_greed_period * 24), ctx.bar_index + 1): # Check ~2 days back
            # This is a simplification. In reality, you'd need a way to get past feature values,
            # or rely on the single current value if the feature updates daily.
            # For backtesting, ctx.features.get("fear_greed_index") will give the value for the *current* bar's day.
            # We will use the prev_fear_greed_index to detect change from the previous day's value.
            pass # Placeholder, logic below relies on self.prev_fear_greed_index

        # Only proceed if we have a previous fear_greed_index to compare
        if self.prev_fear_greed_index is None:
            self.prev_fear_greed_index = current_fear_greed
            return None
        
        signal_confidence = 0.0
        signal_direction = "hold"
        signal_reason = ""
        
        # Long entry conditions
        long_condition_1 = current_fear_greed > 20 and self.prev_fear_greed_index <= 20 # Recovering from Extreme Fear
        long_condition_2 = current_fear_greed > 40 and self.prev_fear_greed_index <= 40 # Recovering from Fear
        
        # Price action confirmation: RSI oversold or price above EMA
        price_confirmation_long = (rsi_val < 40 and closes[-1] > closes[-2]) or (closes[-1] > ema_val)

        # Regime filter: avoid trading in crisis or strong bear trend for longs
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        regime_filter_long = market_regime not in ["CRISIS", "MELTDOWN"] and trend_regime != "bear"

        if not ctx.has_position():
            if (long_condition_1 or long_condition_2) and price_confirmation_long and regime_filter_long:
                signal_direction = "long"
                signal_confidence = 0.7 + (current_fear_greed / 100 * 0.2) # Higher confidence with better recovery
                signal_reason = "sentiment_recovery_long"
                self.last_trade_bar = ctx.bar_index
                
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    metadata={
                        "reason": signal_reason,
                        "fear_greed_current": current_fear_greed,
                        "fear_greed_prev": self.prev_fear_greed_index,
                        "rsi": rsi_val,
                        "ema": ema_val,
                        "price": closes[-1],
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                    }
                )
        
        # Exit condition: if sentiment deteriorates significantly or becomes extremely greedy
        if ctx.has_position() and ctx.position_direction() == "long":
            # If sentiment drops back into extreme fear, or goes into extreme greed (>75)
            if current_fear_greed < 20 or current_fear_greed > 75:
                signal_direction = "flat"
                signal_reason = "sentiment_deterioration_or_extreme_greed"
                return ctx.signal(
                    signal_direction,
                    confidence=0.6,
                    metadata={
                        "reason": signal_reason,
                        "fear_greed_current": current_fear_greed,
                        "fear_greed_prev": self.prev_fear_greed_index,
                        "rsi": rsi_val,
                        "ema": ema_val,
                        "price": closes[-1],
                    }
                )
        
        self.prev_fear_greed_index = current_fear_greed
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        pass