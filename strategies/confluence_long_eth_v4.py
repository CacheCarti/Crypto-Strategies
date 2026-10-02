from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluence(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_entry_thresh = 37.0
        self.rsi_exit_thresh = 58.0
        self.fg_thresh = 40.0
        self.funding_thresh = 0.00002
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _rsi(self, closes, period=12):
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
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        current_price = ctx.bar.close

        # Position exit logic
        if ctx.has_position():
            if current_rsi >= self.rsi_exit_thresh or funding > 0.00025:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "triple_edge_normalization_exit",
                        "rsi": round(current_rsi, 2),
                        "funding_rate": round(funding, 6),
                        "fear_greed": round(fear_greed, 1),
                        "price": current_price,
                    }
                )
            return None

        # Entry cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Confluence check: oversold price + crowded shorts + market fear
        rsi_oversold = current_rsi <= self.rsi_entry_thresh
        funding_negative = funding <= self.funding_thresh
        sentiment_fear = fear_greed <= self.fg_thresh

        if rsi_oversold and funding_negative and sentiment_fear:
            rsi_factor = max(0.0, (self.rsi_entry_thresh - current_rsi) / self.rsi_entry_thresh)
            fg_factor = max(0.0, (self.fg_thresh - fear_greed) / self.fg_thresh)
            confidence = min(0.95, max(0.60, 0.65 + 0.20 * rsi_factor + 0.15 * fg_factor))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_confluence_long",
                    "rsi": round(current_rsi, 2),
                    "fear_greed": round(fear_greed, 1),
                    "funding_rate": round(funding, 6),
                    "price": current_price,
                }
            )

        return None