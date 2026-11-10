from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityBreakout(Strategy):
    METADATA = {
        "name": "Volatility Breakout ETHUSDC",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60, # Need enough bars for ATR and SMA calculations
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.vol_squeeze_bars = 10 # Number of bars for volatility compression check
        self.breakout_threshold_atr = 1.5 # How many ATRs above/below SMA to confirm breakout
        self.cooldown_bars = 10 # Cooldown period after a trade (in bars)
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _atr(self, highs, lows, closes, period=14):
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
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate average ATR over the squeeze period
        recent_atrs = []
        for i in range(self.atr_period + 1, self.warmup_bars):
            atr_val = self._atr(highs[:i], lows[:i], closes[:i], self.atr_period)
            if atr_val is not None:
                recent_atrs.append(atr_val)

        if len(recent_atrs) < self.vol_squeeze_bars + 1: # Need enough history for squeeze check
            return None

        # Volatility squeeze detection: current ATR is significantly lower than recent average
        avg_recent_atr = self._sma(recent_atrs[-self.vol_squeeze_bars-1:-1], self.vol_squeeze_bars)
        if avg_recent_atr is None:
            return None

        is_vol_squeezed = current_atr < (avg_recent_atr * 0.7) # Current ATR is 30% lower than avg

        # Price range for breakout detection
        sma_closes = self._sma(closes, self.atr_period)
        if sma_closes is None:
            return None

        upper_band = sma_closes + (self.breakout_threshold_atr * current_atr)
        lower_band = sma_closes - (self.breakout_threshold_atr * current_atr)

        # Check for breakout after a squeeze
        if is_vol_squeezed:
            if ctx.bar.close > upper_band and not ctx.has_position():
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_expansion_long",
                        "current_atr": current_atr,
                        "avg_recent_atr": avg_recent_atr,
                        "sma_closes": sma_closes,
                        "upper_band": upper_band,
                        "close": ctx.bar.close,
                        "regime": ctx.regime
                    }
                )

            elif ctx.bar.close < lower_band and not ctx.has_position():
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_expansion_short",
                        "current_atr": current_atr,
                        "avg_recent_atr": avg_recent_atr,
                        "sma_closes": sma_closes,
                        "lower_band": lower_band,
                        "close": ctx.bar.close,
                        "regime": ctx.regime
                    }
                )
        
        # If we have a position, check for exit conditions (trailing stop/take profit handled by engine)
        # We can add an additional exit condition here, e.g., if volatility expands too much or reverses
        if ctx.has_position():
            # Example: exit if price re-enters the 'squeeze' range or reverses significantly
            # For this strategy, we primarily rely on the engine's SL/TP.
            # No explicit exit signal here, let the engine manage.
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could adapt parameters here, but for simplicity, we just log
        pass