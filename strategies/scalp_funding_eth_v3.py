from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingTiltedScalper(Strategy):
    METADATA = {
        "name": "FundingTiltedScalper",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 10
        self.rsi_oversold = 18.0
        self.rsi_overbought = 82.0
        self.cooldown_bars = 48  # 4 hours hard cooldown on 5m bars to keep trades between 30-80
        self.cooldown_until = 0

    def _calc_rsi(self, closes: list[float], period: int = 10) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(-diff)

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Filter out crisis regime to prevent catastrophic whipsaws
        if ctx.regime == "crisis" or ctx.market.get("regime") == "CRISIS":
            return None

        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._calc_rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_pos = ctx.position_direction()

        # Position management / fast reversion exit
        if ctx.has_position():
            if current_pos == "long" and rsi >= 55.0:
                self.cooldown_until = ctx.bar_index + self.cooldown_bars
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_long_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "close": ctx.bar.close,
                    },
                )
            elif current_pos == "short" and rsi <= 45.0:
                self.cooldown_until = ctx.bar_index + self.cooldown_bars
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_short_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Hard multi-bar cooldown check
        if ctx.bar_index < self.cooldown_until:
            return None

        # Long entry: strictly negative funding (shorts paying) + severe oversold capitulation
        if funding < -0.00001 and rsi <= self.rsi_oversold:
            self.cooldown_until = ctx.bar_index + self.cooldown_bars
            conf = min(0.95, 0.70 + abs(funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=round(conf, 3),
                stop_loss_bps=100.0,
                take_profit_bps=180.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "negative_funding_extreme_oversold_scalp",
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "close": ctx.bar.close,
                },
            )

        # Short entry: meaningfully elevated positive funding (>0.015% per 8h) + extreme overbought exhaustion
        if funding > 0.00015 and rsi >= self.rsi_overbought:
            self.cooldown_until = ctx.bar_index + self.cooldown_bars
            conf = min(0.95, 0.70 + funding * 1000.0)
            return ctx.signal(
                "short",
                confidence=round(conf, 3),
                stop_loss_bps=100.0,
                take_profit_bps=180.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "high_positive_funding_extreme_overbought_scalp",
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "close": ctx.bar.close,
                },
            )

        return None