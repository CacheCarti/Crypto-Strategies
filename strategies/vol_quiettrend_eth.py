from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrendETH(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Intended for multi-hour holds
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.trend_period = 30
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + self.trend_period)
        highs = ctx.highs(self.vol_period + self.trend_period)
        lows = ctx.lows(self.vol_period + self.trend_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Volatility check (using standard deviation of returns)
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = self._stddev(returns[-self.vol_period:], self.vol_period)
        if volatility is None:
            return None
        
        # Define 'low volatility' threshold - this will require tuning.
        # For ETH 1h, 0.005 (0.5%) might be a reasonable starting point for 'low'
        # Or, compare to a longer-term average volatility
        long_term_vol = self._stddev(returns, len(returns))
        if long_term_vol is None:
            long_term_vol = 0.005 # Default if not enough data

        # Entry condition: Low volatility
        is_low_volatility = volatility < (long_term_vol * 0.75) # Current vol is 25% lower than long-term avg

        # Trend check (SMA crossover or price above/below SMA)
        sma_trend = self._sma(closes, self.trend_period)
        if sma_trend is None:
            return None

        # Regime check (using ctx.regime)
        is_calm_regime = ctx.regime == "calm" or ctx.regime == "normal"

        # Trading logic
        if not ctx.has_position():
            # Long entry: price crosses above SMA during low volatility and calm regime
            # Also check previous bar to ensure a crossover, not just being above
            prev_closes = ctx.closes(self.trend_period + 1)
            prev_sma_trend = self._sma(prev_closes[:-1], self.trend_period)

            if is_low_volatility and is_calm_regime and prev_sma_trend is not None:
                if current_close > sma_trend and prev_closes[-2] <= prev_sma_trend: # Crossover
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "low_vol_calm_regime_sma_crossover_long",
                            "current_close": current_close,
                            "sma_trend": sma_trend,
                            "volatility": volatility,
                            "long_term_vol": long_term_vol,
                            "regime": ctx.regime,
                        }
                    )
                # Short entry: price crosses below SMA during low volatility and calm regime
                elif current_close < sma_trend and prev_closes[-2] >= prev_sma_trend: # Crossover
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "low_vol_calm_regime_sma_crossover_short",
                            "current_close": current_close,
                            "sma_trend": sma_trend,
                            "volatility": volatility,
                            "long_term_vol": long_term_vol,
                            "regime": ctx.regime,
                        }
                    )
        else:
            # Exit condition: volatility expansion or regime change
            if not is_low_volatility or not is_calm_regime:
                # If we have a position, and volatility expands or regime becomes turbulent, exit
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "volatility_expansion_or_regime_change_exit",
                        "current_close": current_close,
                        "volatility": volatility,
                        "long_term_vol": long_term_vol,
                        "is_low_volatility": is_low_volatility,
                        "regime": ctx.regime,
                        "is_calm_regime": is_calm_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set last_signal_bar on exit signal, but this is a good place
        # to add any adaptive logic based on position outcome.
        pass