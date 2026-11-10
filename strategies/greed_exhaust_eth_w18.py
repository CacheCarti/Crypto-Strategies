from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentExhaustionShort(Strategy):
    METADATA = {
        "name": "Sentiment Exhaustion Short",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.momentum_period = 5
        self.min_fear_greed_for_short = 75 # Threshold for "greed"
        self.max_momentum_decline_pct = 0.005 # Price decline from peak in momentum period
        self.cooldown_bars = 5 # Bars to wait after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade immediately

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
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Feature: Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Price Momentum Stalling/Reversal
        # Check for a recent peak and subsequent small decline
        recent_highs = ctx.highs(self.momentum_period)
        if len(recent_highs) < self.momentum_period:
            return None
        
        peak_price_in_momentum = max(recent_highs)
        
        # Condition 1: High Greed
        is_greedy = fear_greed_index >= self.min_fear_greed_for_short

        # Condition 2: Price momentum stall/decline
        # We look for price to be below the recent peak by a small percentage,
        # indicating a stall or minor reversal after a strong run.
        # Also, ensure price is below a short-term EMA, suggesting a downtick.
        momentum_stalling = False
        if peak_price_in_momentum > 0:
            price_decline_from_peak = (peak_price_in_momentum - current_price) / peak_price_in_momentum
            if price_decline_from_peak > 0.001 and price_decline_from_peak < self.max_momentum_decline_pct:
                # Also check if current price is below the fast EMA, indicating a short-term dip
                fast_ema = self._ema(closes, self.fast_ema_period)
                if fast_ema and current_price < fast_ema:
                    momentum_stalling = True

        # Condition 3: Price is above the slow EMA, indicating a prior uptrend
        # This ensures we are trying to short exhaustion in an uptrend, not a downtrend.
        slow_ema = self._ema(closes, self.slow_ema_period)
        above_slow_ema = False
        if slow_ema and current_price > slow_ema:
            above_slow_ema = True

        # Short Entry Logic: High Greed AND Momentum Stalling AND Prior Uptrend
        if not ctx.has_position():
            if is_greedy and momentum_stalling and above_slow_ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(1.0, (fear_greed_index - self.min_fear_greed_for_short) / (100 - self.min_fear_greed_for_short) + 0.2), # Scale confidence based on greed level
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_exhaustion_short",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "peak_price_momentum_period": peak_price_in_momentum,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                    }
                )

        # Exit Logic:
        # 1. Fear & Greed normalizes (drops significantly)
        # 2. Momentum resumes (price recovers above fast EMA)
        if ctx.has_position() and ctx.position_direction() == "short":
            exit_reason = None

            # Greed normalization (e.g., FGI drops below a certain threshold)
            if fear_greed_index < self.min_fear_greed_for_short - 10: # 10 point drop from entry threshold
                exit_reason = "fear_greed_normalized"

            # Price recovery / momentum resumes
            fast_ema = self._ema(closes, self.fast_ema_period)
            if fast_ema and current_price > fast_ema * 1.005: # Price recovers 0.5% above fast EMA
                 exit_reason = "momentum_resumes"

            if exit_reason:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "fast_ema": fast_ema,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not using this for adaptive logic in this strategy, but could be used
        # to adjust confidence or cooldown based on recent trade outcomes.
        pass