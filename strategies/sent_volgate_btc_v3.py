from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolgateBTC(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # 6 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 14
        self.volatility_period = 20
        self.rsi_period = 7 # Faster RSI for entry
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store previous fear/greed state for transition detection
        self.prev_fear_greed_index = None
        self.prev_regime = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Indicators
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Volatility as a gate
        volatility = self._stddev(closes, self.volatility_period)
        avg_volatility = self._sma([self._stddev(closes[i:i+self.volatility_period], self.volatility_period) for i in range(len(closes) - self.volatility_period + 1)], self.volatility_period)
        
        # Ensure avg_volatility is not None and not zero to avoid division by zero
        if volatility is None or avg_volatility is None or avg_volatility == 0:
            return None
        
        volatility_ratio = volatility / avg_volatility
        
        # Sentiment from Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Determine sentiment regime
        sentiment_regime = "neutral"
        if fear_greed_index <= 25:
            sentiment_regime = "extreme_fear"
        elif 25 < fear_greed_index <= 45:
            sentiment_regime = "fear"
        elif 55 < fear_greed_index <= 75:
            sentiment_regime = "greed"
        elif fear_greed_index > 75:
            sentiment_regime = "extreme_greed"

        # Trading logic
        signal_direction = None
        confidence = 0.0
        reason = "no_signal"
        signal_metadata = {}

        # Long entry conditions
        # Extreme Fear + High Volatility + RSI Oversold
        if sentiment_regime == "extreme_fear" and volatility_ratio > 1.2 and rsi < 30:
            # Only trigger on transition from less extreme fear or neutral
            if self.prev_regime not in ["extreme_fear", "fear"]:
                signal_direction = "long"
                confidence = 0.7 + min(0.3, (30 - rsi) / 20) # Higher confidence for deeper oversold
                reason = "extreme_fear_vol_spike_rsi_oversold"
                signal_metadata = {
                    "fear_greed": fear_greed_index,
                    "vol_ratio": volatility_ratio,
                    "rsi": rsi
                }
        # Fear + Moderate Volatility + RSI Oversold
        elif sentiment_regime == "fear" and volatility_ratio > 1.05 and rsi < 35:
            if self.prev_regime not in ["fear", "extreme_fear"]:
                signal_direction = "long"
                confidence = 0.6 + min(0.2, (35 - rsi) / 20)
                reason = "fear_vol_spike_rsi_oversold"
                signal_metadata = {
                    "fear_greed": fear_greed_index,
                    "vol_ratio": volatility_ratio,
                    "rsi": rsi
                }

        # Short entry conditions
        # Extreme Greed + High Volatility + RSI Overbought
        elif sentiment_regime == "extreme_greed" and volatility_ratio > 1.2 and rsi > 70:
            # Only trigger on transition from less extreme greed or neutral
            if self.prev_regime not in ["extreme_greed", "greed"]:
                signal_direction = "short"
                confidence = 0.7 + min(0.3, (rsi - 70) / 20) # Higher confidence for deeper overbought
                reason = "extreme_greed_vol_spike_rsi_overbought"
                signal_metadata = {
                    "fear_greed": fear_greed_index,
                    "vol_ratio": volatility_ratio,
                    "rsi": rsi
                }
        # Greed + Moderate Volatility + RSI Overbought
        elif sentiment_regime == "greed" and volatility_ratio > 1.05 and rsi > 65:
            if self.prev_regime not in ["greed", "extreme_greed"]:
                signal_direction = "short"
                confidence = 0.6 + min(0.2, (rsi - 65) / 20)
                reason = "greed_vol_spike_rsi_overbought"
                signal_metadata = {
                    "fear_greed": fear_greed_index,
                    "vol_ratio": volatility_ratio,
                    "rsi": rsi
                }

        # Update previous states for transition detection
        self.prev_fear_greed_index = fear_greed_index
        self.prev_regime = sentiment_regime

        # Exit logic
        if ctx.has_position():
            # If current sentiment shifts against position, consider exiting
            current_direction = ctx.position_direction()
            if current_direction == "long" and (sentiment_regime == "greed" or sentiment_regime == "extreme_greed"):
                signal_direction = "flat"
                confidence = 0.5
                reason = "sentiment_shift_against_long"
                signal_metadata = {"current_fear_greed": fear_greed_index, "current_regime": sentiment_regime}
            elif current_direction == "short" and (sentiment_regime == "fear" or sentiment_regime == "extreme_fear"):
                signal_direction = "flat"
                confidence = 0.5
                reason = "sentiment_shift_against_short"
                signal_metadata = {"current_fear_greed": fear_greed_index, "current_regime": sentiment_regime}
            
            # If RSI moves back to neutral/overbought for long, or neutral/oversold for short
            if current_direction == "long" and rsi > 50:
                # Only exit if not already exiting due to sentiment shift
                if signal_direction != "flat":
                    signal_direction = "flat"
                    confidence = 0.4
                    reason = "rsi_recovered_from_oversold_long"
                    signal_metadata = {"rsi": rsi}
            elif current_direction == "short" and rsi < 50:
                if signal_direction != "flat":
                    signal_direction = "flat"
                    confidence = 0.4
                    reason = "rsi_recovered_from_overbought_short"
                    signal_metadata = {"rsi": rsi}

            # If we decided to flat, mark the last trade bar
            if signal_direction == "flat":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(signal_direction, confidence=confidence, metadata=signal_metadata)
            else:
                return ctx.signal("hold") # Hold if no new exit signal

        # Issue new entry signal if determined
        if signal_direction in ["long", "short"]:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=signal_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position close
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass