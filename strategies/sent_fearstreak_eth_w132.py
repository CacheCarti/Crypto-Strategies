from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class SentimentStabilizationStrategy(Strategy):
    METADATA = {
        "name": "Sentiment Stabilization Long",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.fg_lookback = 5 # days
        self.fg_threshold = 30 # Fear & Greed index
        self.cooldown_bars = 10
        self.bars_since_last_trade = self.cooldown_bars

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
        
        # Calculate initial averages
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.long_ema_period + self.rsi_period) # Ensure enough data for both
        highs = ctx.highs(self.long_ema_period + self.rsi_period)
        lows = ctx.lows(self.long_ema_period + self.rsi_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate indicators
        long_ema = self._ema(closes, self.long_ema_period)
        short_ema = self._ema(closes, self.short_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if long_ema is None or short_ema is None or rsi is None:
            return None

        # Sentiment check: Is Fear & Greed index depressed for multiple days?
        # Fear & Greed is daily, so we check the last N readings.
        # We assume ctx.features.get("fear_greed_index") provides the current daily value.
        # To get historical daily values, we would need a more complex mechanism (e.g., storing daily values in initialize)
        # For simplicity, we assume the current ctx.features.get("fear_greed_index") represents the 'daily' value for the current bar's day.
        # This will be constant for 24 bars (1h) or 288 bars (5m).
        
        current_fg_index = ctx.features.get("fear_greed_index", 50)
        
        # We need to simulate checking 'multiple daily readings'. Since FG is daily,
        # we can check if the current value is below threshold AND the market has been
        # in a 'fear' state recently.
        # This is a simplification, as we don't have direct access to *past days'* FG values
        # on a bar-by-bar basis. We'll use the current FG index as a proxy for 'depressed sentiment'.
        
        sentiment_depressed = current_fg_index <= self.fg_threshold

        # Price stabilization check:
        # A simple stabilization could be a short EMA crossing above a longer EMA
        # after a period of depressed sentiment, or RSI moving up from oversold.
        
        # Condition 1: Short EMA crosses above Long EMA (stabilization)
        # We need to check if the cross just happened.
        # Previous bar's EMAs:
        prev_closes = ctx.closes(self.long_ema_period + self.rsi_period + 1)
        if len(prev_closes) < self.long_ema_period + self.rsi_period + 1:
            return None
        
        prev_long_ema = self._ema(prev_closes[:-1], self.long_ema_period)
        prev_short_ema = self._ema(prev_closes[:-1], self.short_ema_period)

        ema_cross_up = (prev_short_ema < prev_long_ema and short_ema > long_ema)
        
        # Condition 2: RSI shows signs of recovery from oversold
        # RSI was oversold (e.g., below 30) and is now moving up, but not yet overbought.
        rsi_was_oversold_and_recovering = (rsi > 30 and self._rsi(prev_closes[:-1], self.rsi_period) <= 30)

        # Entry Logic:
        # Long only if sentiment is depressed, there's a price stabilization signal,
        # and we are not currently in a position, and cooldown is over.
        if not ctx.has_position() and self.bars_since_last_trade >= self.cooldown_bars:
            if sentiment_depressed and (ema_cross_up or rsi_was_oversold_and_recovering):
                self.bars_since_last_trade = 0 # Reset cooldown
                
                reason_detail = ""
                if ema_cross_up and rsi_was_oversold_and_recovering:
                    reason_detail = "ema_cross_up_and_rsi_recovering"
                elif ema_cross_up:
                    reason_detail = "ema_cross_up"
                elif rsi_was_oversold_and_recovering:
                    reason_detail = "rsi_recovering_from_oversold"

                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": f"sentiment_depressed_stabilization_{reason_detail}",
                        "fear_greed_index": current_fg_index,
                        "long_ema": long_ema,
                        "short_ema": short_ema,
                        "rsi": rsi,
                        "current_price": ctx.bar.close,
                    }
                )

        # Exit Logic: Flat if conditions reverse or position is held for too long.
        # If we have a position, we check for exit signals.
        if ctx.has_position():
            # Example exit: if short EMA crosses back below long EMA, or RSI gets overbought
            if short_ema < long_ema and ctx.position_direction() == "long":
                 return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_bearish_cross_exit",
                        "long_ema": long_ema,
                        "short_ema": short_ema,
                        "rsi": rsi,
                        "current_price": ctx.bar.close,
                    }
                )
            
            if rsi > 70 and ctx.position_direction() == "long":
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "long_ema": long_ema,
                        "short_ema": short_ema,
                        "rsi": rsi,
                        "current_price": ctx.bar.close,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.bars_since_last_trade = 0
        # Optional: Log position details or adapt strategy based on performance
        # ctx.log(f"Position closed: {position['direction']} return {position['return_bps']} bps, held for {position['bars_held']} bars.")