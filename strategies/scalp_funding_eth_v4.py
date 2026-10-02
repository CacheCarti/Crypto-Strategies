from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltedRsiScalp(Strategy):
    METADATA = {
        "name": "FundingTiltedRsiScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 9
        self.rsi_oversold = 20.0
        self.rsi_overbought = 80.0
        self.rsi_exit_long = 54.0
        self.rsi_exit_short = 46.0
        self.funding_long_thresh = -0.00003  # genuine negative funding discount
        self.funding_short_thresh = 0.00020  # distinct high funding premium
        self.cooldown_bars = 42  # 3.5 hours mandatory cooldown between trades
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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
        # Avoid erratic scalping during severe market breakdown
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.6:
            return None

        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position Management & Fast Mean-Reversion Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi >= self.rsi_exit_long:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "rsi_mean_reverted_long_exit",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                    },
                )
            elif direction == "short" and rsi <= self.rsi_exit_short:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "rsi_mean_reverted_short_exit",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                    },
                )
            return None

        # Hard Cooldown Guard after any exit to strictly constrain trade count
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High-Conviction Long Setup: Significant negative funding + Extreme 5m oversold
        if funding < self.funding_long_thresh and rsi <= self.rsi_oversold:
            funding_scale = min(abs(funding) * 8000.0, 0.25)
            confidence = min(0.70 + funding_scale, 0.95)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=95.0,
                take_profit_bps=190.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "funding_negative_extreme_rsi_oversold",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                },
            )

        # High-Conviction Short Setup: Squeezed positive funding + Extreme 5m overbought
        if funding > self.funding_short_thresh and rsi >= self.rsi_overbought:
            funding_scale = min((funding - self.funding_short_thresh) * 8000.0, 0.25)
            confidence = min(0.70 + funding_scale, 0.95)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=95.0,
                take_profit_bps=190.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "funding_positive_extreme_rsi_overbought",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                },
            )

        return None