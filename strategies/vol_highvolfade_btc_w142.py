from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFade(Strategy):
    METADATA = {
        "name": "VolatilityFade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,
        "required_features": [], # No specific features needed, using OHLCV + context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

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
        return self._sma(trs, period) # Use SMA for ATR

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.zscore_period * 2) # Need more history for Z-score and ATR
        highs = ctx.highs(self.zscore_period * 2)
        lows = ctx.lows(self.zscore_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None
        
        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        
        if current_zscore is None or current_atr is None:
            return None

        # Volatility filter: only trade in top quartile of volatility
        # We need a historical perspective on ATR to determine top quartile.
        # For simplicity and to fit within line limits, we'll use a dynamic threshold
        # based on a multiple of the current ATR relative to its recent average.
        # A more robust approach would pre-calculate historical ATR quartiles in initialize.
        
        # For now, let's use a simple heuristic: ATR must be above a certain absolute threshold
        # or significantly above its recent past.
        # To avoid over-trading, we'll use a relative threshold based on the average ATR.
        
        # For a simple dynamic threshold, we can compare current ATR to a longer-term average ATR.
        long_term_atr_period = self.volatility_period * 2
        long_term_atr = self._atr(highs, lows, closes, long_term_atr_period)
        
        if long_term_atr is None or long_term_atr == 0:
            return None

        # Condition for high volatility: current ATR is at least 1.25 times the long-term average ATR
        is_high_volatility = current_atr > (long_term_atr * 1.25)
        
        # Determine confidence
        confidence = 0.0

        if not ctx.has_position():
            # Long entry: Fade significant negative Z-score in high volatility
            if current_zscore < -1.5 and is_high_volatility:
                confidence = min(0.9, abs(current_zscore) / 2.0) # Scale confidence with z-score extremity
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "zscore_oversold_fade_high_volatility",
                        "z_score": current_zscore,
                        "current_atr": current_atr,
                        "long_term_atr": long_term_atr,
                        "price": ctx.bar.close,
                    }
                )
            # Short entry: Fade significant positive Z-score in high volatility
            elif current_zscore > 1.5 and is_high_volatility:
                confidence = min(0.9, abs(current_zscore) / 2.0) # Scale confidence with z-score extremity
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "zscore_overbought_fade_high_volatility",
                        "z_score": current_zscore,
                        "current_atr": current_atr,
                        "long_term_atr": long_term_atr,
                        "price": ctx.bar.close,
                    }
                )
        elif ctx.has_position():
            # Exit logic: If z-score crosses back towards zero or beyond, consider exiting
            pos_direction = ctx.position_direction()
            entry_zscore = ctx.position.get("metadata", {}).get("z_score")

            if pos_direction == "long":
                # Exit long if z-score recovers significantly or turns positive
                if current_zscore > -0.5: # Moved from extreme negative to near neutral/positive
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_position_zscore_recovery",
                            "entry_zscore": entry_zscore,
                            "current_zscore": current_zscore,
                            "price": ctx.bar.close,
                        }
                    )
            elif pos_direction == "short":
                # Exit short if z-score recovers significantly or turns negative
                if current_zscore < 0.5: # Moved from extreme positive to near neutral/negative
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_position_zscore_recovery",
                            "entry_zscore": entry_zscore,
                            "current_zscore": current_zscore,
                            "price": ctx.bar.close,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar on position close to ensure cooldown applies
        self.last_trade_bar = ctx.bar_index
        pass