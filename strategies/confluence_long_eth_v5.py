from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TripleEdgeConfluence(Strategy):
    METADATA = {
        "name": "Triple Edge Confluence",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_oversold = 36.0
        self.rsi_exit_target = 58.0
        self.fng_max_entry = 38.0
        self.funding_max_entry = -0.00001
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        fear_greed = float(ctx.features.get("fear_greed_index", 50.0))
        funding_rate = float(ctx.features.get("funding_rate_ethusdt", 0.0))
        regime = ctx.market.get("regime", "NORMAL")

        if ctx.has_position():
            # Check exit conditions for open long position
            if current_rsi >= self.rsi_exit_target or fear_greed >= 55.0 or funding_rate >= 0.0003:
                reason = "rsi_rebound" if current_rsi >= self.rsi_exit_target else "sentiment_normalized"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "rsi": round(current_rsi, 2),
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate,
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme meltdown states
        if regime == "MELTDOWN":
            return None

        # Triple edge stack: negative funding + fear regime + oversold RSI
        is_funding_short = funding_rate <= self.funding_max_entry
        is_fear = fear_greed <= self.fng_max_entry
        is_oversold = current_rsi <= self.rsi_oversold

        if is_funding_short and is_fear and is_oversold:
            # Scale confidence based on confluence depth
            rsi_factor = max(0.0, (self.rsi_oversold - current_rsi) / 15.0)
            fear_factor = max(0.0, (self.fng_max_entry - fear_greed) / 25.0)
            confidence = min(0.95, 0.65 + 0.15 * rsi_factor + 0.15 * fear_factor)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=550.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "triple_edge_crowd_short_fear_oversold",
                    "rsi": round(current_rsi, 2),
                    "fear_greed": fear_greed,
                    "funding_rate": funding_rate,
                    "regime": regime,
                    "price": ctx.bar.close,
                },
            )

        return None