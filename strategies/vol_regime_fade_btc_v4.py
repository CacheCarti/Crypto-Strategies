from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityConditionedFade(Strategy):
    METADATA = {
        "name": "Volatility Conditioned Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60, # Need enough for ATR, Z-score, and volatility quartile
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 30 # For historical volatility
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store historical volatilities to determine quartiles
        self.historical_volatilities = []
        self.max_vol_history = 200 # Keep a rolling window of volatilities

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _get_volatility_quartile(self, current_volatility):
        if len(self.historical_volatilities) < self.volatility_period: # Need enough history to rank
            return None

        sorted_vols = sorted(self.historical_volatilities)
        q1_index = math.ceil(len(sorted_vols) * 0.25) - 1
        q3_index = math.ceil(len(sorted_vols) * 0.75) - 1

        if current_volatility >= sorted_vols[q3_index]:
            return "high"
        elif current_volatility <= sorted_vols[q1_index]:
            return "low"
        return "medium"


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score
        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None
        
        # Calculate ATR as a proxy for realized volatility
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        # Update historical volatilities
        self.historical_volatilities.append(current_atr)
        if len(self.historical_volatilities) > self.max_vol_history:
            self.historical_volatilities.pop(0)

        volatility_quartile = self._get_volatility_quartile(current_atr)

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic: normalize at z-score near zero
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            
            # Check for Z-score normalization
            if abs(current_zscore) < 0.5: # Z-score has normalized
                reason = "zscore_normalization_exit"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_zscore": current_zscore, "entry_price": entry_price, "current_price": current_price})
            
            # Timed stop (if not already closed by SL/TP)
            if ctx.bar_index - ctx.position["entry_bar_index"] > self.METADATA["declared_hold_seconds"] / 3600: # Convert seconds to bars
                 reason = "timed_stop_exit"
                 return ctx.signal("flat", confidence=0.5, metadata={"reason": reason, "bars_held": ctx.bar_index - ctx.position["entry_bar_index"], "entry_price": entry_price, "current_price": current_price})

            return None # Hold position if exit conditions not met

        # Entry logic: Volatility-Conditioned Fade
        # Only fade if volatility is high
        if volatility_quartile != "high":
            return None # Stand down in low/medium volatility

        # Z-score thresholds for "stretched moves"
        long_entry_zscore = -1.5 # Price is significantly below its mean
        short_entry_zscore = 1.5  # Price is significantly above its mean

        # Funding rate as a directional bias filter for fades
        # Positive funding rate suggests long bias, good for short fades
        # Negative funding rate suggests short bias, good for long fades
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        signal_confidence = 0.6 # Base confidence

        # Long Entry: Price significantly below mean AND high volatility AND funding rate supports long fade
        if current_zscore < long_entry_zscore and funding_rate < 0.0001: # Small positive funding or negative
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=signal_confidence,
                metadata={
                    "reason": "volatility_conditioned_long_fade",
                    "current_zscore": current_zscore,
                    "zscore_threshold": long_entry_zscore,
                    "current_atr": current_atr,
                    "volatility_quartile": volatility_quartile,
                    "funding_rate": funding_rate,
                    "price": current_price
                }
            )

        # Short Entry: Price significantly above mean AND high volatility AND funding rate supports short fade
        elif current_zscore > short_entry_zscore and funding_rate > -0.0001: # Small negative funding or positive
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=signal_confidence,
                metadata={
                    "reason": "volatility_conditioned_short_fade",
                    "current_zscore": current_zscore,
                    "zscore_threshold": short_entry_zscore,
                    "current_atr": current_atr,
                    "volatility_quartile": volatility_quartile,
                    "funding_rate": funding_rate,
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close, just logging.
        pass