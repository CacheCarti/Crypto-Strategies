from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateBTC(Strategy):
    METADATA = {
        "name": "SentimentVolGateBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.sma_vol_period = 20
        self.fear_greed_period = 5 # How many bars to consider FGI stable
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.sma_vol_period * 2) # Need enough for ATR and SMA
        highs = ctx.highs(self.sma_vol_period * 2)
        lows = ctx.lows(self.sma_vol_period * 2)

        if len(closes) < self.METADATA["warmup_bars"] or ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Volatility filter using ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None

        # Compare current ATR to its historical average to gauge volatility
        # Using a longer period for SMA of ATR to identify volatility regimes
        atr_history = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) 
                       for i in range(self.atr_period, len(closes))]
        atr_history = [a for a in atr_history if a is not None]
        
        if len(atr_history) < self.sma_vol_period:
            return None
        
        avg_atr = self._sma(atr_history, self.sma_vol_period)

        if avg_atr is None or avg_atr == 0:
            return None

        vol_ratio = atr / avg_atr

        # Sentiment from Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50.0)

        # Trading logic
        signal = None
        confidence = 0.0
        metadata = {}

        if not ctx.has_position():
            # Long entry: Extreme Fear + High Volatility (Panic)
            if fear_greed_index <= 25 and vol_ratio > 1.2: # Fear and Volatility above average
                # Additional condition: price action confirms potential reversal (e.g., current bar closed higher than open)
                if ctx.bar.close > ctx.bar.open and closes[-2] < closes[-1]: # Green candle and upturn
                    confidence = min(0.6 + (25 - fear_greed_index) * 0.01, 0.9)
                    signal = "long"
                    metadata = {
                        "reason": "extreme_fear_high_vol_long",
                        "fear_greed_index": fear_greed_index,
                        "vol_ratio": vol_ratio,
                        "current_price": current_price,
                        "atr": atr,
                        "avg_atr": avg_atr,
                    }

            # Short entry: Extreme Greed + High Volatility (Euphoria)
            elif fear_greed_index >= 75 and vol_ratio > 1.2: # Greed and Volatility above average
                # Additional condition: price action confirms potential reversal (e.g., current bar closed lower than open)
                if ctx.bar.close < ctx.bar.open and closes[-2] > closes[-1]: # Red candle and downturn
                    confidence = min(0.6 + (fear_greed_index - 75) * 0.01, 0.9)
                    signal = "short"
                    metadata = {
                        "reason": "extreme_greed_high_vol_short",
                        "fear_greed_index": fear_greed_index,
                        "vol_ratio": vol_ratio,
                        "current_price": current_price,
                        "atr": atr,
                        "avg_atr": avg_atr,
                    }
        else: # Position is open, consider exit conditions
            # Simple exit: if sentiment reverses significantly against position or volatility drops
            if ctx.position_direction() == "long":
                if fear_greed_index > 50 or vol_ratio < 0.8: # Neutral/Greed or Volatility drops
                    signal = "flat"
                    confidence = 0.5
                    metadata = {
                        "reason": "long_exit_sentiment_reversal_or_vol_drop",
                        "fear_greed_index": fear_greed_index,
                        "vol_ratio": vol_ratio,
                        "current_price": current_price,
                    }
            elif ctx.position_direction() == "short":
                if fear_greed_index < 50 or vol_ratio < 0.8: # Neutral/Fear or Volatility drops
                    signal = "flat"
                    confidence = 0.5
                    metadata = {
                        "reason": "short_exit_sentiment_reversal_or_vol_drop",
                        "fear_greed_index": fear_greed_index,
                        "vol_ratio": vol_ratio,
                        "current_price": current_price,
                    }

        if signal:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata,
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass