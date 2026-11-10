from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.prev_rsi = 50.0

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period

        for i in range(period + 1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gain = max(diff, 0.0)
            loss = max(-diff, 0.0)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 20)
        if len(closes) < self.rsi_period + 5:
            return None

        rsi = self._calc_rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position management & early thesis invalidation
        if ctx.has_position():
            # Exit if RSI reaches healthy overbought/normalization level, or funding becomes heavily positive
            if rsi >= 60.0 or funding > 0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "thesis_normalized_or_overextended",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "fear_greed": fear_greed
                    }
                )
            self.prev_rsi = rsi
            return None

        # Check cooldown constraint
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_rsi = rsi
            return None

        # Redesigned Event-Boundary: Trigger precisely on the dynamic reversal upward (RSI crosses above 30 from oversold)
        # while negative funding (crowd short) and macro fear regime (< 38) are active.
        rsi_reversal_trigger = (self.prev_rsi <= 32.0 and rsi > self.prev_rsi and rsi < 42.0)
        is_crowd_short = (funding < 0.0)
        is_fear_regime = (fear_greed < 38.0)

        self.prev_rsi = rsi

        if rsi_reversal_trigger and is_crowd_short and is_fear_regime:
            # Scale confidence based on depth of negative funding
            confidence = 0.65
            if funding < -0.0001:
                confidence = 0.85

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_oversold_bounce_crowd_short",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fear_greed,
                    "close": ctx.bar.close
                }
            )

        return None