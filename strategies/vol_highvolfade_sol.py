from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolFade(Strategy):
    METADATA = {
        "name": "SolVolFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Aim for a few hours hold
        "warmup_bars": 60, # Need enough bars for 20-period std dev and z-score
        "required_features": [], # No external features needed for this hypothesis
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.zscore_threshold = 2.0 # Z-score for fading
        self.min_volatility_quantile = 0.75 # Top 25% volatility to fade
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _avg_true_range(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1) # Need one extra for ATR calc
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Calculate Z-score of price movement
        price_zscore = self._zscore(closes, self.vol_period)
        if price_zscore is None:
            return None

        # Calculate volatility using ATR
        atr = self._avg_true_range(highs, lows, closes, self.vol_period)
        if atr is None:
            return None

        # Use the market context's "regime" for volatility filtering
        # The hypothesis is to fade in "volatile" or "crisis" regimes, but not "calm"
        # We also need to define what "top-quartile volatility" means
        # For simplicity, we'll use the 'regime' as a proxy for volatility bands.
        # "volatile" and "crisis" are high-volatility regimes.
        is_high_volatility_regime = ctx.market.get("regime", "NORMAL") in ["VOLATILE", "CRISIS", "HIGH_VOL"]

        if not ctx.has_position():
            # Long signal: fade extreme downside Z-score in high volatility
            if price_zscore < -self.zscore_threshold and is_high_volatility_regime:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "sol_zscore_fade_long",
                        "price_zscore": price_zscore,
                        "current_atr": atr,
                        "market_regime": ctx.market.get("regime", "NORMAL"),
                        "close": current_close
                    }
                )

            # Short signal: fade extreme upside Z-score in high volatility
            if price_zscore > self.zscore_threshold and is_high_volatility_regime:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "sol_zscore_fade_short",
                        "price_zscore": price_zscore,
                        "current_atr": atr,
                        "market_regime": ctx.market.get("regime", "NORMAL"),
                        "close": current_close
                    }
                )
        else:
            # If we have a position, hold it.
            # The engine will manage SL/TP.
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after position closes
        self.last_trade_bar = ctx.bar_index