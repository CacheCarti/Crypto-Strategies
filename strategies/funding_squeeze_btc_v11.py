from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_position = 0
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 5:
            return None

        current_price = ctx.bar.close
        price_24h_ago = closes[-self.lookback]
        price_return_24h = (current_price - price_24h_ago) / price_24h_ago
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._rsi(closes, 14) or 50.0

        # Position management
        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()

            # Time-based active exit or structural squeeze resolution
            if self.bars_in_position >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_hold_reached", "bars": self.bars_in_position})

            if direction == "long" and (rsi > 78.0 or funding > 0.0002):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_squeeze_exhausted", "rsi": rsi, "funding": funding})

            if direction == "short" and (rsi < 22.0 or funding < -0.0002):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_squeeze_exhausted", "rsi": rsi, "funding": funding})

            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Squeeze Divergence Setup
        # Long Squeeze: Price is pushing up (strong momentum >= 1.0%), but funding is deeply negative (crowded shorts trapped)
        long_setup = (price_return_24h >= 0.010) and (funding <= -0.00003) and (rsi < 70.0)

        # Short Squeeze: Price is breaking down (momentum <= -1.0%), but funding is stubbornly positive (crowded longs trapped)
        short_setup = (price_return_24h <= -0.010) and (funding >= 0.00003) and (rsi > 30.0)

        if long_setup:
            confidence = min(0.85, 0.60 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding_rate": funding,
                    "price_return_24h": price_return_24h,
                    "rsi": rsi,
                    "price": current_price,
                }
            )

        if short_setup:
            confidence = min(0.85, 0.60 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_squeeze_continuation",
                    "funding_rate": funding,
                    "price_return_24h": price_return_24h,
                    "rsi": rsi,
                    "price": current_price,
                }
            )

        return None