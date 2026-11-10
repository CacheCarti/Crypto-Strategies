from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateEth(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 7
        self.vol_period = 20
        self.cooldown_bars = 12 # Cooldown after a trade
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

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Apply smoothing for subsequent values
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate indicators
        rsi_val = self._rsi(closes, self.short_period)
        sma_long = self._sma(closes, self.long_period)
        volatility = self._stddev(closes, self.vol_period)
        
        if rsi_val is None or sma_long is None or volatility is None:
            return None

        # Market regime and sentiment
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Volatility thresholds (heuristic, adjust based on backtest)
        # Assuming higher volatility indicates panic/euphoria
        avg_vol = self._sma([self._stddev(ctx.closes(i+1), self.vol_period) for i in range(self.vol_period, len(closes))], self.vol_period)
        if avg_vol is None:
            avg_vol = 0.0 # Fallback for early bars
        
        high_vol_threshold = avg_vol * 1.5 if avg_vol > 0 else 0.005 # Example: 50% above average or a fixed minimum
        low_vol_threshold = avg_vol * 0.75 if avg_vol > 0 else 0.001 # Example: 25% below average or a fixed minimum

        # Sentiment-Volatility Gated Entry Logic
        current_price = ctx.bar.close

        # Long Entry: Fearful sentiment + high volatility + oversold RSI + price above SMA
        if not ctx.has_position() and \
           fear_greed_index < 30 and \
           volatility > high_vol_threshold and \
           rsi_val < 35 and \
           current_price > sma_long:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "fear_high_vol_rsi_oversold_sma_support",
                    "fear_greed_index": fear_greed_index,
                    "volatility": volatility,
                    "high_vol_threshold": high_vol_threshold,
                    "rsi": rsi_val,
                    "sma_long": sma_long,
                    "price": current_price
                }
            )

        # Short Entry: Greedy sentiment + high volatility + overbought RSI + price below SMA
        if not ctx.has_position() and \
           fear_greed_index > 70 and \
           volatility > high_vol_threshold and \
           rsi_val > 65 and \
           current_price < sma_long:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "greed_high_vol_rsi_overbought_sma_resistance",
                    "fear_greed_index": fear_greed_index,
                    "volatility": volatility,
                    "high_vol_threshold": high_vol_threshold,
                    "rsi": rsi_val,
                    "sma_long": sma_long,
                    "price": current_price
                }
            )

        # Exit logic (if holding a position)
        if ctx.has_position():
            # If sentiment becomes ambiguous or volatility drops significantly, consider exiting
            if (35 <= fear_greed_index <= 65 and volatility < low_vol_threshold):
                # Only exit if the position is not strongly profitable, to avoid cutting winners
                # Or if it's losing and the regime shifts to ambiguity
                if ctx.position["confidence"] > 0.0: # Check if position is valid
                    return_on_position = (current_price - ctx.position["entry_price"]) / ctx.position["entry_price"] * 10000 if ctx.position["direction"] == "long" else (ctx.position["entry_price"] - current_price) / ctx.position["entry_price"] * 10000
                    
                    if return_on_position < 50 or return_on_position < -50: # Exit if not strongly profitable or losing
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.5,
                            metadata={
                                "reason": "ambiguous_sentiment_low_vol_exit",
                                "fear_greed_index": fear_greed_index,
                                "volatility": volatility,
                                "low_vol_threshold": low_vol_threshold,
                                "rsi": rsi_val,
                                "price": current_price,
                                "return_bps": return_on_position
                            }
                        )
            # Add a time-based exit for very long holds if needed, though TP/SL should handle it
            if ctx.bar_index - self.last_trade_bar > (self.METADATA["declared_hold_seconds"] / 3600 * 24 * 1.5): # 1.5x declared hold bars
                 if ctx.position["confidence"] > 0.0:
                    return_on_position = (current_price - ctx.position["entry_price"]) / ctx.position["entry_price"] * 10000 if ctx.position["direction"] == "long" else (ctx.position["entry_price"] - current_price) / ctx.position["entry_price"] * 10000
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.3, # Lower confidence as it's a time-based exit
                        metadata={
                            "reason": "max_hold_time_exit",
                            "bars_held": ctx.bar_index - self.last_trade_bar,
                            "price": current_price,
                            "return_bps": return_on_position
                        }
                    )


        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position close
        # This ensures the cooldown is respected even if position is closed by engine SL/TP
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass