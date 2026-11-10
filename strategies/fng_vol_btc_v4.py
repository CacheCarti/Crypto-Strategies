from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedVolBTC(Strategy):
    METADATA = {
        "name": "FearGreedVolBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fng_low_threshold = 30  # Extreme Fear / Fear boundary
        self.fng_recovery_threshold = 45 # Exit on sentiment recovery
        self.vol_period = 20
        self.vol_high_threshold = 0.025 # Example: 2.5% daily vol (adjust)
        self.vol_low_threshold = 0.015 # Exit on vol normalization
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        if len(closes) < self.vol_period + 1:
            return None

        # Calculate realized volatility (daily std dev of log returns)
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        daily_vol = self._stddev(log_returns, self.vol_period) * math.sqrt(252) # Ann. vol

        if daily_vol is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry logic
        if not ctx.has_position():
            if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
                return None

            # Condition 1: Fear & Greed Index is low (fearful)
            is_fearful = fear_greed_index <= self.fng_low_threshold

            # Condition 2: Realized volatility is elevated (panic)
            is_high_vol = daily_vol >= self.vol_high_threshold

            # Combine conditions for entry
            if is_fearful and is_high_vol:
                # Add a price-based condition to avoid trading on flat price action
                # Simple condition: current bar is red (weakness) or close is near low
                is_weakness = ctx.bar.close < ctx.bar.open or (ctx.bar.close - ctx.bar.low) / (ctx.bar.high - ctx.bar.low + 1e-9) < 0.3

                if is_weakness:
                    confidence = 0.6 + (1 - (fear_greed_index / 50)) * 0.4 # Higher confidence when more fearful
                    return ctx.signal(
                        "long",
                        confidence=min(1.0, confidence),
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "fear_greed_low_high_vol_weakness",
                            "fear_greed_index": fear_greed_index,
                            "daily_vol": daily_vol,
                            "vol_threshold": self.vol_high_threshold,
                            "fng_threshold": self.fng_low_threshold,
                            "price_weakness": is_weakness,
                        }
                    )
        # Exit logic
        elif ctx.position_direction() == "long":
            # Exit 1: Sentiment recovery
            if fear_greed_index >= self.fng_recovery_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "sentiment_recovery_exit",
                        "fear_greed_index": fear_greed_index,
                        "recovery_threshold": self.fng_recovery_threshold,
                        "daily_vol": daily_vol,
                    }
                )
            # Exit 2: Volatility normalization
            if daily_vol <= self.vol_low_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "volatility_normalization_exit",
                        "daily_vol": daily_vol,
                        "normalization_threshold": self.vol_low_threshold,
                        "fear_greed_index": fear_greed_index,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index