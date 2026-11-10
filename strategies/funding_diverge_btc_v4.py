from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # Aim for 2-3 hour hold on 1h bars
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.funding_rate_threshold = 0.0001  # e.g., 0.01% per 8h funding
        self.funding_lookback = 5 # bars to check funding trend
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_lookback)]
        # Ensure we have enough funding rate data for lookback
        if len(funding_rates) < self.funding_lookback:
            return None
        
        current_funding = funding_rates[-1]
        
        # Market regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid trading in highly volatile or crisis regimes
        if regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.5:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: Price rising, but funding negative (short squeeze potential)
        # Price rising: fast EMA above slow EMA and price above fast EMA
        # Funding negative: current funding rate is below a threshold
        if (fast_ema > slow_ema and current_price > fast_ema and
            current_funding < -self.funding_rate_threshold and
            trend_regime != "bear"): # Avoid long in bearish trend
            
            if not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add a touch of randomness
                reason = "funding_divergence_long_short_squeeze"
            elif ctx.position_direction() == "short": # Flip from short to long
                signal_direction = "long"
                confidence = 0.8 + ctx.rng.uniform(-0.1, 0.1)
                reason = "funding_divergence_flip_short_to_long"

        # Short condition: Price falling, but funding positive (long liquidation potential)
        # Price falling: fast EMA below slow EMA and price below fast EMA
        # Funding positive: current funding rate is above a threshold
        elif (fast_ema < slow_ema and current_price < fast_ema and
              current_funding > self.funding_rate_threshold and
              trend_regime != "bull"): # Avoid short in bullish trend
            
            if not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1)
                reason = "funding_divergence_short_long_liquidation"
            elif ctx.position_direction() == "long": # Flip from long to short
                signal_direction = "short"
                confidence = 0.8 + ctx.rng.uniform(-0.1, 0.1)
                reason = "funding_divergence_flip_long_to_short"

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, max(0.0, confidence)), # Clamp confidence
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "funding_rate": current_funding,
                    "regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just log for debugging
        pass