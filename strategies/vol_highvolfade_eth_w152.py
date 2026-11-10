from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeZScore(Strategy):
    METADATA = {
        "name": "Volatility Fade Z-Score",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60, # Need enough bars for 20-period Z-score and 30-period volatility
        "required_features": [], # No external features used for entry logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 30
        self.cooldown_bars = 10 # Cooldown period after a position closes
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade on first bar

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

    def _volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        if len(returns) < period:
            return None
        return self._stddev(returns, period)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        current_volatility = self._volatility(closes, self.volatility_period)

        if current_zscore is None or current_volatility is None:
            return None

        # Determine volatility quartile (simple approximation)
        # We need historical volatility to assess quartiles, but we can't store it across bars directly
        # For simplicity, we'll use a fixed threshold or a moving average of volatility
        # Let's use a dynamic threshold based on the average volatility
        
        # Calculate a simple moving average of volatility for context
        # This is a proxy for the 'top-quartile' condition without needing to store a long history of volatilities
        # In a real scenario, we'd pre-calculate and store historical quartiles.
        # Here, we'll use a relative comparison to a recent average.
        volatility_lookback = 60 # Period to average volatility for comparison
        all_closes_for_vol = ctx.closes(self.volatility_period + volatility_lookback)
        
        if len(all_closes_for_vol) < self.volatility_period + volatility_lookback:
            return None
            
        recent_volatilities = []
        for i in range(volatility_lookback):
            vol_slice = all_closes_for_vol[i : i + self.volatility_period +1]
            if len(vol_slice) == self.volatility_period + 1:
                vol = self._volatility(vol_slice, self.volatility_period)
                if vol is not None:
                    recent_volatilities.append(vol)
        
        if not recent_volatilities:
            return None
        
        avg_recent_volatility = sum(recent_volatilities) / len(recent_volatilities)
        
        # Condition 1: High Volatility (top-quartile proxy)
        # We define "top-quartile" as current volatility being significantly above its recent average
        is_high_volatility = current_volatility > avg_recent_volatility * 1.25 # 25% above average for top quartile

        # Condition 2: Z-score extension (event boundary)
        # Fade extreme moves: short if z-score is very high, long if very low
        zscore_threshold_long = -2.0
        zscore_threshold_short = 2.0

        # Condition 3: Regime filter - do not fade calm trends
        # We fade (trade against) extended moves. If the market is in a strong "bull" or "bear" trend,
        # fading might be dangerous. We want to fade in "normal" or "volatile" regimes, not strong trends.
        trend_regime = ctx.market.get("trend_regime", "neutral")
        is_calm_trend = trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.5
        
        if ctx.has_position():
            return None # Hold existing position

        # Entry logic
        if is_high_volatility and not is_calm_trend:
            if current_zscore < zscore_threshold_long:
                # Long signal: price is significantly oversold in a high volatility, non-trending environment
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "zscore_fade_long_high_volatility",
                        "current_zscore": current_zscore,
                        "zscore_threshold": zscore_threshold_long,
                        "current_volatility": current_volatility,
                        "avg_recent_volatility": avg_recent_volatility,
                        "trend_regime": trend_regime,
                        "price": ctx.bar.close,
                    }
                )
            elif current_zscore > zscore_threshold_short:
                # Short signal: price is significantly overbought in a high volatility, non-trending environment
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "zscore_fade_short_high_volatility",
                        "current_zscore": current_zscore,
                        "zscore_threshold": zscore_threshold_short,
                        "current_volatility": current_volatility,
                        "avg_recent_volatility": avg_recent_volatility,
                        "trend_regime": trend_regime,
                        "price": ctx.bar.close,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar on position close to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # You could also use the position data for adaptive sizing, but for now, just logging
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']} (Return: {position['return_bps']} bps, Held: {position['bars_held']} bars)")