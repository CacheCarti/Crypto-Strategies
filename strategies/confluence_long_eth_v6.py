from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluence(Strategy):
    METADATA = {
        "name": "Triple Edge Confluence Swing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_oversold = 36.0
        self.rsi_exit = 58.0
        self.fear_greed_thresh = 38.0
        self.funding_max_thresh = 0.00002
        self.funding_exit_thresh = 0.00035
        self.cooldown_bars = 5
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.rsi_period + 1:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        current_price = ctx.bar.close

        # Position exit management on edge normalization
        if ctx.has_position():
            if ctx.position_direction() == "long":
                rsi_normalized = rsi_val >= self.rsi_exit
                funding_overcrowded = funding >= self.funding_exit_thresh
                if rsi_normalized or funding_overcrowded:
                    self.last_exit_bar = ctx.bar_index
                    reason = "rsi_normalized" if rsi_normalized else "funding_overcrowded_exit"
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": reason,
                            "rsi": round(rsi_val, 2),
                            "funding_rate": round(funding, 6),
                            "fear_greed_index": fg_index,
                            "price": round(current_price, 2)
                        }
                    )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Confluence Entry Conditions
        edge_funding = funding <= self.funding_max_thresh
        edge_sentiment = fg_index <= self.fear_greed_thresh
        edge_technicals = rsi_val <= self.rsi_oversold

        if edge_funding and edge_sentiment and edge_technicals:
            # Scaled conviction based on severity of oversold & negative funding
            oversold_bonus = max(0.0, (self.rsi_oversold - rsi_val) / self.rsi_oversold) * 0.2
            funding_bonus = 0.1 if funding < 0.0 else 0.0
            base_confidence = 0.65
            confidence = min(0.95, base_confidence + oversold_bonus + funding_bonus)

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_confluence_funding_sentiment_rsi",
                    "rsi": round(rsi_val, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed_index": fg_index,
                    "price": round(current_price, 2)
                }
            )

        return None