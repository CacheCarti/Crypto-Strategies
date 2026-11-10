from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 16
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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # In-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Dynamic normalization exit: RSI snaps back or funding shifts strongly positive
            if rsi >= 55.0 or funding >= 0.00015 or bars_held >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_time_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Entry gating
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis / Meltdown protection
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Rare Triple-Edge Confluence: Negative funding, Fear, and Price Oversold
        funding_edge = funding <= -0.00002
        sentiment_edge = fg_index <= 38.0
        oversold_edge = rsi <= 35.0

        if funding_edge and sentiment_edge and oversold_edge:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.65 + max(0.0, (35.0 - rsi) / 70.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                },
            )

        return None