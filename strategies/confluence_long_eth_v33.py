from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        current_rsi = self._rsi(closes, 14)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Handle existing position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalization exit: RSI reaches mean or above, or extreme hold time elapsed
            if current_rsi >= 55.0 or funding > 0.0001 or bars_held >= 36:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_horizon",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                    },
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Robust regime gate: avoid acute meltdown where bottoms cascade relentlessly
        if market_regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Triple-stack confluence conditions
        cond_funding = funding <= 0.0
        cond_sentiment = fear_greed <= 38.0
        cond_rsi = current_rsi <= 36.0

        if cond_funding and cond_sentiment and cond_rsi:
            # Scaled conviction based on severity of oversold/negative funding
            confidence = 0.65
            if funding < -0.00005:
                confidence += 0.15
            if current_rsi < 30.0:
                confidence += 0.10
            confidence = min(0.95, confidence)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_stack_confluence_dip_buyer",
                    "rsi": round(current_rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": round(fear_greed, 1),
                    "regime": market_regime,
                },
            )

        return None