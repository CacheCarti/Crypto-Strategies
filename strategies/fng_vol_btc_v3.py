from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FngVolBtcWeakness(Strategy):
    METADATA = {
        "name": "FngVolBtcWeakness",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fng_low_threshold = 30  # Fear & Greed index for "low"
        self.fng_recovery_threshold = 45 # Fear & Greed index for "recovery"
        self.vol_period = 20 # Period for realized volatility (Std Dev of returns)
        self.vol_high_multiplier = 1.5 # Volatility is high if above this multiple of its recent average
        self.vol_norm_multiplier = 0.8 # Volatility is normalized if below this multiple of its recent average
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade on first bar

        # To track volatility average
        self.volatility_history = []
        self.volatility_avg_period = 100 # Long period for average volatility

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _calculate_returns_std(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(1, len(closes)):
            returns.append((closes[i] - closes[i-1]) / closes[i-1])
        return self._stddev(returns, period) * 10000 # Convert to basis points for readability

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        if len(closes) < self.vol_period + 1:
            return None

        current_fng = ctx.features.get("fear_greed_index", 50)
        current_vol = self._calculate_returns_std(closes, self.vol_period)

        if current_vol is None:
            return None

        # Update volatility history for adaptive threshold
        self.volatility_history.append(current_vol)
        if len(self.volatility_history) > self.volatility_avg_period:
            self.volatility_history.pop(0)

        # Calculate average volatility
        if len(self.volatility_history) < self.volatility_avg_period / 2: # Need enough data for a stable average
            return None
        avg_vol = sum(self.volatility_history) / len(self.volatility_history)

        vol_high_threshold = avg_vol * self.vol_high_multiplier
        vol_norm_threshold = avg_vol * self.vol_norm_multiplier

        # Entry logic: Buy weakness when FNG is low and volatility is high (panic)
        if not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None

            # Conditions for entry
            is_fng_low = current_fng <= self.fng_low_threshold
            is_vol_elevated = current_vol >= vol_high_threshold

            if is_fng_low and is_vol_elevated:
                confidence = 0.6 + (1 - (current_fng / 100)) * 0.4 # Higher confidence when FNG is lower
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(1.0, confidence),
                    metadata={
                        "reason": "fng_low_vol_elevated_buy_weakness",
                        "fng_index": current_fng,
                        "current_vol": current_vol,
                        "avg_vol": avg_vol,
                        "vol_threshold": vol_high_threshold
                    }
                )
        else:
            # Exit logic: Volatility normalization or sentiment recovery
            if ctx.position_direction() == "long":
                is_vol_normalized = current_vol <= vol_norm_threshold
                is_fng_recovered = current_fng >= self.fng_recovery_threshold

                if is_vol_normalized:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "volatility_normalized_exit",
                            "fng_index": current_fng,
                            "current_vol": current_vol,
                            "avg_vol": avg_vol,
                            "vol_threshold": vol_norm_threshold
                        }
                    )
                if is_fng_recovered:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "sentiment_recovered_exit",
                            "fng_index": current_fng,
                            "current_vol": current_vol,
                            "avg_vol": avg_vol
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        pass