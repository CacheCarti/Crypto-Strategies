from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedVolatilityReversion(Strategy):
    METADATA = {
        "name": "Fear Greed Volatility Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.rsi_period = 14
        self.entry_cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.entry_cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _rsi(self, closes, period):
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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Calculate indicators
        current_rsi = self._rsi(closes, self.rsi_period)
        volatility = self._stddev(closes, self.volatility_period)

        if current_rsi is None or volatility is None:
            return None

        # Get market context and features
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Determine volatility thresholds dynamically or fixed
        # For simplicity, let's use fixed thresholds for now, but dynamic could be based on historical volatility percentiles
        volatility_threshold_high = 0.015 * ctx.bar.close # e.g., 1.5% stddev over 20 bars
        volatility_threshold_low = 0.005 * ctx.bar.close # e.g., 0.5% stddev over 20 bars

        # Entry Logic: Buy weakness (RSI oversold) when Fear & Greed is low (Extreme Fear/Fear) AND
        # volatility is elevated (indicating panic/capitulation)
        if not ctx.has_position():
            if (ctx.bar_index - self.last_trade_bar) < self.entry_cooldown_bars:
                return None # Cooldown active

            if (fear_greed_index <= 30 and # Extreme Fear / Fear
                current_rsi < 35 and
                volatility > volatility_threshold_high):

                # Confidence scales with how low RSI and Fear & Greed are, and how high volatility is
                confidence = min(1.0, (35 - current_rsi) / 35 + (30 - fear_greed_index) / 30 + (volatility / volatility_threshold_high - 1) / 2)
                confidence = max(0.2, min(0.9, confidence)) # Clamp confidence

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "fear_greed_volatility_reversion_long",
                        "fear_greed_index": fear_greed_index,
                        "rsi": current_rsi,
                        "volatility": volatility,
                        "volatility_threshold_high": volatility_threshold_high,
                        "entry_price": ctx.bar.close
                    }
                )

        # Exit Logic: Exit long position if volatility normalizes or sentiment recovers,
        # or if RSI goes overbought (mean reversion)
        if ctx.has_position() and ctx.position_direction() == "long":
            # Exit if fear subsides (sentiment recovery)
            if fear_greed_index >= 50: # Neutral or Greed
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "sentiment_recovery_exit",
                        "fear_greed_index": fear_greed_index,
                        "rsi": current_rsi,
                        "volatility": volatility,
                        "exit_price": ctx.bar.close
                    }
                )
            
            # Exit if volatility normalizes (panic subsides)
            if volatility < volatility_threshold_low:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "volatility_normalization_exit",
                        "fear_greed_index": fear_greed_index,
                        "rsi": current_rsi,
                        "volatility": volatility,
                        "volatility_threshold_low": volatility_threshold_low,
                        "exit_price": ctx.bar.close
                    }
                )

            # Exit if RSI goes overbought (mean reversion target hit)
            if current_rsi > 70:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "fear_greed_index": fear_greed_index,
                        "rsi": current_rsi,
                        "volatility": volatility,
                        "exit_price": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to current bar_index to enforce cooldown after any position closure
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        # For simplicity, we just log for now.
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps in {position['bars_held']} bars. Reason: {position.get('exit_reason', 'unknown')}")