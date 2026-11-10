from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class SentimentVolatilityGateSol(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60, # Need enough bars for ATR and SMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.sma_period = 20
        self.fg_neutral_range = (40, 60) # Fear & Greed neutral range
        self.fg_extreme_fear_threshold = 25
        self.fg_extreme_greed_threshold = 75
        self.volatility_multiplier = 1.5 # How many ATRs above/below SMA to consider "panic/euphoria"
        self.min_volatility_threshold = 0.01 # Minimum ATR value to avoid division by zero or tiny moves
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate ATR for volatility
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None or current_atr < self.min_volatility_threshold:
            return None

        # Calculate SMA of closes for trend/mean reference
        sma_closes = self._sma(closes, self.sma_period)
        if sma_closes is None:
            return None

        # Volatility-adjusted deviation from SMA
        deviation = current_price - sma_closes
        volatility_threshold_abs = current_atr * self.volatility_multiplier

        # Conditions for entry
        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Extreme fear + price significantly below SMA (panic confirmed by price action)
        if fear_greed_index < self.fg_extreme_fear_threshold and deviation < -volatility_threshold_abs:
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = 0.75
                reason = "extreme_fear_volatility_confirmed_long"
                metadata = {
                    "fg_index": fear_greed_index,
                    "price_deviation_from_sma": deviation,
                    "volatility_threshold_atr": volatility_threshold_abs,
                    "current_atr": current_atr,
                    "sma_closes": sma_closes
                }

        # Short signal: Extreme greed + price significantly above SMA (euphoria confirmed by price action)
        elif fear_greed_index > self.fg_extreme_greed_threshold and deviation > volatility_threshold_abs:
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = 0.75
                reason = "extreme_greed_volatility_confirmed_short"
                metadata = {
                    "fg_index": fear_greed_index,
                    "price_deviation_from_sma": deviation,
                    "volatility_threshold_atr": volatility_threshold_abs,
                    "current_atr": current_atr,
                    "sma_closes": sma_closes
                }

        # Stand down in ambiguous sentiment (neutral F&G) or low volatility
        if self.fg_neutral_range[0] <= fear_greed_index <= self.fg_neutral_range[1]:
            # If we have a position, consider exiting if sentiment becomes neutral
            if ctx.has_position():
                signal_direction = "flat"
                confidence = 0.5
                reason = "sentiment_neutral_exit"
                metadata = {
                    "fg_index": fear_greed_index,
                    "current_price": current_price
                }
            else:
                return None # No signal if neutral sentiment and no position

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing details or reset state if needed
        # For this strategy, the cooldown is handled in on_bar, so no specific action here
        pass