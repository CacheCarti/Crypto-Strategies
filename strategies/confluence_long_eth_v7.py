from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TripleConfluenceReversion(Strategy):
    METADATA = {
        "name": "Triple Confluence Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_oversold = 36.0
        self.rsi_exit = 58.0
        self.fear_greed_threshold = 38
        self.funding_threshold = 0.0
        self.cooldown_bars = 6
        self.max_hold_bars = 18

        self.last_exit_bar = -999
        self.entry_bar = -1

    def _rsi(self, closes: list, period: int = 12) -> Optional[float]:
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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 20)
        if len(closes) < self.rsi_period + 5:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        has_pos = ctx.has_position()

        if has_pos:
            bars_held = (
                ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            )

            rsi_rebound = rsi >= self.rsi_exit
            funding_normalized = funding_rate > 0.0001 and rsi >= 50.0
            time_exhaustion = bars_held >= self.max_hold_bars

            if rsi_rebound or funding_normalized or time_exhaustion:
                reason = "rsi_target_reached"
                if time_exhaustion:
                    reason = "max_hold_time_reached"
                elif funding_normalized:
                    reason = "funding_crowd_neutralized"

                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "rsi": round(rsi, 2),
                        "funding_rate": funding_rate,
                        "bars_held": bars_held,
                        "close_price": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Confluence Entry Conditions
        funding_edge = funding_rate <= self.funding_threshold
        sentiment_edge = fear_greed <= self.fear_greed_threshold
        technical_edge = rsi <= self.rsi_oversold

        if funding_edge and sentiment_edge and technical_edge:
            conf_boost = 0.0
            if rsi < 28.0:
                conf_boost += 0.15
            if funding_rate < -0.00005:
                conf_boost += 0.1
            confidence = min(0.95, 0.70 + conf_boost)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "fear_greed": fear_greed,
                    "entry_price": ctx.bar.close,
                },
            )

        return None