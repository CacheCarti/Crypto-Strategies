from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostCrashDrift(Strategy):
    METADATA = {
        "name": "Post-Crash Drift Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours avg hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 10  # Lookback for sharp flush
        self.stabilization_period = 5  # Bars to wait for stabilization
        self.recovery_period = 5  # Bars to confirm recovery drift
        self.cooldown_bars = 20 # Cooldown period after a trade
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.flush_period + self.stabilization_period + self.recovery_period)
        highs = ctx.highs(self.flush_period + self.stabilization_period + self.recovery_period)
        lows = ctx.lows(self.flush_period + self.stabilization_period + self.recovery_period)

        if len(closes) < self.flush_period + self.stabilization_period + self.recovery_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime filter: Only trade in normal or volatile regimes, avoid crisis
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None
        
        # Trend filter: Only consider long in neutral or bull trend
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        # 1. Detect a sharp flush (significant drop followed by increased volatility)
        # Calculate recent return
        flush_closes = closes[:self.flush_period]
        if len(flush_closes) < self.flush_period:
            return None
        
        flush_return = (flush_closes[-1] - flush_closes[0]) / flush_closes[0]
        
        # Calculate volatility during flush period
        flush_volatility = self._stddev(flush_closes, self.flush_period)
        
        # Thresholds for flush: e.g., >2% drop and high volatility
        # These thresholds need tuning based on asset and timeframe
        is_flush = flush_return < -0.02 and flush_volatility is not None and flush_volatility > 0.005 # Example values

        if not is_flush:
            return None

        # 2. Stabilization phase: price stops dropping sharply
        stabilization_closes = closes[self.flush_period : self.flush_period + self.stabilization_period]
        if len(stabilization_closes) < self.stabilization_period:
            return None
        
        # Check if price has stabilized (e.g., small range, not making new lows)
        stabilization_range = max(stabilization_closes) - min(stabilization_closes)
        prev_low = min(lows[self.flush_period - 5 : self.flush_period]) # Lows just before stabilization
        
        is_stabilizing = (ctx.bar.close > stabilization_closes[0] or 
                          ctx.bar.close > prev_low) and \
                         stabilization_range / ctx.bar.close < 0.01 # Small range for stabilization

        if not is_stabilizing:
            return None

        # 3. Recovery drift: price starts slowly moving up
        recovery_closes = closes[self.flush_period + self.stabilization_period :]
        if len(recovery_closes) < self.recovery_period:
            return None
        
        recovery_sma = self._sma(recovery_closes, self.recovery_period)
        
        is_recovering = recovery_sma is not None and ctx.bar.close > recovery_sma

        if is_flush and is_stabilizing and is_recovering and not ctx.has_position():
            confidence = 0.7 + (ctx.rng.uniform(0,1) * 0.2) # Add some randomness for exploration
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "post_crash_recovery_drift",
                    "flush_return": flush_return,
                    "flush_volatility": flush_volatility,
                    "stabilization_range_pct": stabilization_range / ctx.bar.close,
                    "recovery_sma": recovery_sma,
                    "current_price": ctx.bar.close,
                    "regime": ctx.regime,
                    "trend_regime": trend_regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index