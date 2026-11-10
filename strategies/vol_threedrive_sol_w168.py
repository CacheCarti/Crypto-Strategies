from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeStrategy(Strategy):
    METADATA = {
        "name": "Volatility Fade Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 5
        self.rsi_period = 14
        self.atr_period = 14
        self.cooldown_bars = 5 # Cooldown period after a trade

        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + self.rsi_period + 1)
        highs = ctx.highs(self.long_period + self.atr_period + 1)
        lows = ctx.lows(self.long_period + self.atr_period + 1)
        volumes = ctx.volumes(self.long_period + self.atr_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None
        
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        if ctx.has_position():
            return None

        # Calculate indicators
        sma_long = self._sma(closes, self.long_period)
        sma_short = self._sma(closes, self.short_period)
        current_rsi = self._rsi(closes, self.rsi_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if sma_long is None or sma_short is None or current_rsi is None or current_atr is None:
            return None

        current_close = ctx.bar.close

        # Volatility structure: look for multiple directional pushes
        # We define a 'push' as a bar closing above/below its short SMA, and
        # a 'failed push' as a reversal after such a sequence.

        # Look for 3 consecutive closes above short SMA (bullish push)
        # followed by a close below short SMA (failure to continue)
        
        # Bullish fade conditions
        bullish_push_count = 0
        for i in range(1, min(len(closes), 4)): # Check last 3 bars
            prev_closes = closes[-i-1:-i]
            prev_sma_short = self._sma(prev_closes, self.short_period)
            if prev_sma_short is not None and closes[-i] > prev_sma_short:
                bullish_push_count += 1
            else:
                break
        
        # Bearish fade conditions
        bearish_push_count = 0
        for i in range(1, min(len(closes), 4)): # Check last 3 bars
            prev_closes = closes[-i-1:-i]
            prev_sma_short = self._sma(prev_closes, self.short_period)
            if prev_sma_short is not None and closes[-i] < prev_sma_short:
                bearish_push_count += 1
            else:
                break

        # Entry logic for fading the 3rd push
        if bullish_push_count >= 2 and current_close < sma_short and current_rsi > 60: # Fade bullish strength
            # Additional filter: ensure current bar is a clear reversal
            if closes[-1] < closes[-2] and closes[-1] < closes[-3]: # Current bar is lower than previous two
                 # Consider market regime for confidence
                confidence = 0.6
                if ctx.market.get("regime", "NORMAL") == "HIGH_VOL":
                    confidence = 0.75 # Higher confidence in volatile regimes for fading
                elif ctx.market.get("regime", "NORMAL") == "CRISIS":
                    confidence = 0.8
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "fade_bullish_push_reversal",
                        "bullish_pushes": bullish_push_count,
                        "current_close": current_close,
                        "sma_short": sma_short,
                        "current_rsi": current_rsi,
                        "market_regime": ctx.market.get("regime", "NORMAL")
                    }
                )

        if bearish_push_count >= 2 and current_close > sma_short and current_rsi < 40: # Fade bearish weakness
            # Additional filter: ensure current bar is a clear reversal
            if closes[-1] > closes[-2] and closes[-1] > closes[-3]: # Current bar is higher than previous two
                # Consider market regime for confidence
                confidence = 0.6
                if ctx.market.get("regime", "NORMAL") == "HIGH_VOL":
                    confidence = 0.75 # Higher confidence in volatile regimes for fading
                elif ctx.market.get("regime", "NORMAL") == "CRISIS":
                    confidence = 0.8

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "fade_bearish_push_reversal",
                        "bearish_pushes": bearish_push_count,
                        "current_close": current_close,
                        "sma_short": sma_short,
                        "current_rsi": current_rsi,
                        "market_regime": ctx.market.get("regime", "NORMAL")
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the bar index of the close to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # Optional: Adaptive sizing based on recent performance
        # For this strategy, we'll keep it simple and just enforce cooldown.
        pass