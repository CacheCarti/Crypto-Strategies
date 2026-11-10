from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Contrarian Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -100
        self.cooldown_bars = 12  # 12 bars cooldown to ensure controlled trade count
        self.extreme_funding = 0.00035  # 0.035% per 8h funding threshold
        self.norm_funding = 0.00008     # Normalization exit threshold

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
            return 0.0
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Track funding rate over consecutive bars
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Funding persistence check (last 3 bars)
        recent_funding = self.funding_history[-3:] if len(self.funding_history) >= 3 else [funding]
        all_neg = all(f < -self.extreme_funding for f in recent_funding)
        all_pos = all(f > self.extreme_funding for f in recent_funding)

        # Trend & exhaustion indicators
        sma_fast = self._sma(closes, 20)
        sma_slow = self._sma(closes, 50)
        zscore = self._zscore(closes, 20)

        if sma_fast is None or sma_slow is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit when funding normalizes towards balance or price reverts
            if pos_dir == "long" and (funding >= -self.norm_funding or zscore > 1.2):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding": funding,
                    "zscore": round(zscore, 2),
                    "price": ctx.bar.close
                })
            elif pos_dir == "short" and (funding <= self.norm_funding or zscore < -1.2):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding": funding,
                    "zscore": round(zscore, 2),
                    "price": ctx.bar.close
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid crisis illiquidity
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Squeeze Entry: Crowd heavily short & price is not in a runaway waterfall
        # zscore > -2.2 ensures we do not fade into an extreme crash
        if all_neg and zscore > -2.2 and zscore < -0.2:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "negative_funding_squeeze_long",
                    "funding": funding,
                    "zscore": round(zscore, 2),
                    "sma20": round(sma_fast, 2),
                    "price": ctx.bar.close
                }
            )

        # Short Squeeze Fading Entry: Crowd over-leveraged long & price not runaway parabolic
        # zscore < 2.2 ensures we do not short an uncontrolled vertical spike
        if all_pos and zscore < 2.2 and zscore > 0.2:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "positive_funding_exhaustion_short",
                    "funding": funding,
                    "zscore": round(zscore, 2),
                    "sma20": round(sma_fast, 2),
                    "price": ctx.bar.close
                }
            )

        return None