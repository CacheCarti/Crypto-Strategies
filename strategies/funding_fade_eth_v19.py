from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "FundingRateReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_history = []
        self.persistence_needed = 3
        # 0.01% is standard baseline; extreme is >= 0.025% or <= -0.015% per 8h
        self.pos_funding_threshold = 0.00025
        self.neg_funding_threshold = -0.00015
        self.normalization_threshold = 0.00008

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Track persistent funding values
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Check normalization exit when holding position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_normalize_exit = False
            if pos_dir == "long" and funding >= -self.normalization_threshold:
                should_normalize_exit = True
            elif pos_dir == "short" and funding <= self.normalization_threshold:
                should_normalize_exit = True

            if should_normalize_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized",
                        "funding": funding,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Hard cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.funding_history) < self.persistence_needed:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:]
        persistent_negative = all(f <= self.neg_funding_threshold for f in recent_funding)
        persistent_positive = all(f >= self.pos_funding_threshold for f in recent_funding)

        if not persistent_negative and not persistent_positive:
            return None

        # Price filters: avoid fading runaway trends using SMA and RSI
        sma24 = self._sma(closes, 24)
        rsi = self._rsi(closes, 14)
        if sma24 is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Long Setup: funding is heavily negative, but market is not in an outright freefall collapse
        if persistent_negative:
            # Price shouldn't be excessively below SMA (avoid catching falling knives)
            distance_from_sma = (current_price - sma24) / sma24
            if distance_from_sma > -0.04 and rsi > 30.0:
                conf = min(0.85, 0.5 + abs(funding) * 500.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=450.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "short_squeeze_funding_reversion",
                        "funding": funding,
                        "rsi": round(rsi, 2),
                        "distance_from_sma": round(distance_from_sma, 4),
                        "price": current_price,
                    },
                )

        # Short Setup: funding is heavily positive, but market is not in an exponential vertical pump
        if persistent_positive:
            distance_from_sma = (current_price - sma24) / sma24
            if distance_from_sma < 0.04 and rsi < 70.0:
                conf = min(0.85, 0.5 + funding * 500.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=450.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "long_exhaustion_funding_reversion",
                        "funding": funding,
                        "rsi": round(rsi, 2),
                        "distance_from_sma": round(distance_from_sma, 4),
                        "price": current_price,
                    },
                )

        return None