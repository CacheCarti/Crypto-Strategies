from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostPanicDrift(Strategy):
    METADATA = {
        "name": "Post-Panic Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drop_period = 12  # Lookback for sharp drop
        self.stabilization_period = 6  # Bars for low volatility stabilization
        self.ema_period = 20 # EMA for trend confirmation
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filtering
        fear_greed = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Only trade if market is not in crisis and not strongly bearish
        if market_regime == "CRISIS" or trend_regime == "bear":
            return None

        # 1. Look for a sharp multi-bar drop
        # Calculate percentage change over drop_period
        price_drop = (closes[-1] - closes[-self.drop_period]) / closes[-self.drop_period]
        
        # We need a significant drop, e.g., > 3%
        if price_drop > -0.03: # Not a sharp drop
            return None

        # 2. Wait for stabilization (low volatility) after the drop
        # Check volatility of the last stabilization_period bars
        recent_closes = closes[-self.stabilization_period:]
        if len(recent_closes) < self.stabilization_period:
            return None
        
        std_dev = self._stddev(recent_closes, self.stabilization_period)
        if std_dev is None:
            return None

        # Normalize std dev by average price for relative volatility
        avg_price_stabilization = self._sma(recent_closes, self.stabilization_period)
        if avg_price_stabilization is None or avg_price_stabilization == 0:
            return None
        relative_volatility = (std_dev / avg_price_stabilization) * 100 # percentage volatility

        # We want low volatility, e.g., less than 0.5%
        if relative_volatility > 0.5: # Volatility too high, no stabilization
            return None
        
        # 3. Confirm price is above a short-term EMA for a bullish drift
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        if current_price < ema_val: # Price must be above EMA for bullish drift
            return None

        # Entry signal conditions met
        if not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.7, abs(price_drop) * 10 + (1 - relative_volatility / 0.5) * 0.2)
            confidence = max(0.4, min(0.9, confidence)) # Clamp confidence
            
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "post_panic_drift_stabilization",
                    "price_drop_pct": price_drop * 100,
                    "stabilization_volatility_pct": relative_volatility,
                    "current_price": current_price,
                    "ema_20": ema_val,
                    "fear_greed_index": fear_greed
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown period
        self.last_trade_bar = ctx.bar_index
        pass # No adaptive sizing for this strategy currently