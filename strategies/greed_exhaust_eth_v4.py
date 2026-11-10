from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GreedExhaustionShort(Strategy):
    METADATA = {
        "name": "Greed Exhaustion Short",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_slow = 30
        self.ema_period_fast = 10
        self.momentum_period = 5
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period_slow + 1) # Need one more for momentum calc
        if len(closes) < self.ema_period_slow + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Calculate EMAs
        ema_fast = self._ema(closes[:-1], self.ema_period_fast) # Use closes up to previous bar for cleaner signal
        ema_slow = self._ema(closes[:-1], self.ema_period_slow)
        
        if ema_fast is None or ema_slow is None:
            return None

        # Price momentum (rate of change over momentum_period)
        if len(closes) < self.momentum_period + 1:
            return None
        momentum_start_price = closes[-self.momentum_period - 1]
        momentum_end_price = closes[-2] # Momentum up to previous bar
        
        if momentum_start_price == 0: # Avoid division by zero
            price_momentum_pct = 0.0
        else:
            price_momentum_pct = ((momentum_end_price - momentum_start_price) / momentum_start_price) * 100

        # Regime filters
        # Only trade if market is not in a strong bull trend (we are looking for reversals)
        # and not in a crisis state
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        if trend_regime == "bull" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None # Avoid shorting into strong uptrends
        if regime in ["CRISIS", "MELTDOWN"]:
            return None # Avoid trading during extreme market stress

        # --- Entry Logic ---
        if not ctx.has_position():
            # Condition 1: Extreme Greed
            greed_threshold = 80
            if fear_greed_index > greed_threshold:
                # Condition 2: Price has had a strong run (momentum positive)
                strong_momentum_threshold = 1.5 # % change over momentum_period
                if price_momentum_pct > strong_momentum_threshold:
                    # Condition 3: Momentum is stalling/reversing (fast EMA crossing below slow EMA)
                    # We look for the current price to be below the fast EMA, and fast EMA below slow EMA
                    # as an early sign of a turn after the strong run
                    if current_price < ema_fast and ema_fast < ema_slow:
                        confidence = min(0.6 + (fear_greed_index - greed_threshold) / 20 * 0.3, 0.9) # Scale confidence with greed
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            metadata={
                                "reason": "greed_exhaustion_short_momentum_stall",
                                "fear_greed_index": fear_greed_index,
                                "price_momentum_pct": price_momentum_pct,
                                "current_price": current_price,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "trend_regime": trend_regime,
                            }
                        )

        # --- Exit Logic ---
        elif ctx.position_direction() == "short":
            # Exit if greed normalizes (below a threshold)
            if fear_greed_index < 60:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "greed_normalized_exit",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                    }
                )
            
            # Exit if momentum resumes upwards (fast EMA crosses above slow EMA)
            # This is a strong sign the reversal failed or new uptrend started
            if current_price > ema_fast and ema_fast > ema_slow:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "momentum_resumption_exit",
                        "current_price": current_price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can use this for logging or adaptive logic if needed, but not required for this strategy.
        pass