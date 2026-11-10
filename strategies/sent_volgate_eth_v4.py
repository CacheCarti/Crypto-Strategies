from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateEth(Strategy):
    METADATA = {
        "name": "SentimentVolGateEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.sma_vol_period = 30
        self.fear_greed_lookback = 3
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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
        closes = ctx.closes(self.sma_vol_period + self.atr_period + 1)
        highs = ctx.highs(self.sma_vol_period + self.atr_period + 1)
        lows = ctx.lows(self.sma_vol_period + self.atr_period + 1)

        if len(closes) < self.sma_vol_period + self.atr_period + 1:
            return None

        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate historical volatility based on ATR
        # We need ATR values for the last sma_vol_period to calculate SMA of ATR
        atr_values = []
        for i in range(self.atr_period + 1, len(closes) + 1):
            h_slice = highs[i - self.atr_period - 1 : i]
            l_slice = lows[i - self.atr_period - 1 : i]
            c_slice = closes[i - self.atr_period - 1 : i]
            atr_val = self._atr(h_slice, l_slice, c_slice, self.atr_period)
            if atr_val is not None:
                atr_values.append(atr_val)
        
        if len(atr_values) < self.sma_vol_period:
            return None
        
        sma_atr = self._sma(atr_values, self.sma_vol_period)
        if sma_atr is None or sma_atr == 0:
            return None

        volatility_ratio = current_atr / sma_atr

        # Sentiment data
        current_fg = ctx.features.get("fear_greed_index", 50)
        
        # Check for recent significant change in Fear & Greed Index
        # This requires storing past FG values or having a way to access them.
        # Since we don't have direct access to historical feature values, we'll
        # simplify and just use the current value with thresholds.
        
        # Volatility thresholds
        HIGH_VOLATILITY_THRESHOLD = 1.2
        LOW_VOLATILITY_THRESHOLD = 0.8
        
        # Fear & Greed thresholds
        EXTREME_FEAR = 25
        FEAR = 35
        EXTREME_GREED = 75
        GREED = 65

        # Stand down in quiet ambiguous sentiment (neutral F&G, low vol)
        if (FEAR < current_fg < GREED) and (volatility_ratio < HIGH_VOLATILITY_THRESHOLD):
            return None # Ambiguous sentiment or low volatility, stand down

        # Long condition: Extreme Fear + High Volatility (panic selling)
        if current_fg <= EXTREME_FEAR and volatility_ratio >= HIGH_VOLATILITY_THRESHOLD:
            if not ctx.has_position() or ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "extreme_fear_high_vol_long",
                        "fear_greed_index": current_fg,
                        "volatility_ratio": volatility_ratio,
                        "current_atr": current_atr,
                        "sma_atr": sma_atr,
                        "price": ctx.bar.close,
                    }
                )

        # Short condition: Extreme Greed + High Volatility (euphoric buying exhaustion)
        if current_fg >= EXTREME_GREED and volatility_ratio >= HIGH_VOLATILITY_THRESHOLD:
            if not ctx.has_position() or ctx.position_direction() == "long":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "extreme_greed_high_vol_short",
                        "fear_greed_index": current_fg,
                        "volatility_ratio": volatility_ratio,
                        "current_atr": current_atr,
                        "sma_atr": sma_atr,
                        "price": ctx.bar.close,
                    }
                )
        
        # Exit condition: Sentiment reverts to neutral or volatility drops significantly
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # If in long position and sentiment is no longer fearful or volatility drops
            if pos_dir == "long" and (current_fg > FEAR or volatility_ratio < LOW_VOLATILITY_THRESHOLD):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_exit_sentiment_neutralized_or_vol_dropped",
                        "fear_greed_index": current_fg,
                        "volatility_ratio": volatility_ratio,
                        "price": ctx.bar.close,
                    }
                )
            
            # If in short position and sentiment is no longer greedy or volatility drops
            if pos_dir == "short" and (current_fg < GREED or volatility_ratio < LOW_VOLATILITY_THRESHOLD):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_exit_sentiment_neutralized_or_vol_dropped",
                        "fear_greed_index": current_fg,
                        "volatility_ratio": volatility_ratio,
                        "price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close
        pass