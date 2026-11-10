from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_rsi = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.period_rsi + 2)
        if len(closes) < self.period_rsi + 1:
            return None

        rsi = self._rsi(closes, self.period_rsi)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            rsi_normalized = rsi >= 52.0
            sentiment_normalized = fg_index >= 45.0
            funding_normalized = funding > 0.0001
            time_exhausted = bars_held >= 12

            if rsi_normalized or sentiment_normalized or funding_normalized or time_exhausted:
                reason = "rsi_normalized" if rsi_normalized else (
                    "sentiment_normalized" if sentiment_normalized else (
                        "funding_normalized" if funding_normalized else "time_decay_exit"
                    )
                )
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": f"exit_{reason}",
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "fear_greed": round(fg_index, 1),
                        "bars_held": bars_held
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Triple edge conditions
        cond_funding = funding <= 0.0
        cond_sentiment = fg_index <= 35.0
        cond_rsi = rsi <= 35.0

        if cond_funding and cond_sentiment and cond_rsi:
            self.entry_bar = ctx.bar_index
            depth = max(0.0, (35.0 - rsi) / 20.0)
            confidence = min(1.0, 0.65 + depth * 0.25)
            
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_confluence_funding_sentiment_rsi",
                    "rsi": round(rsi, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "confidence": round(confidence, 2)
                }
            )

        return None