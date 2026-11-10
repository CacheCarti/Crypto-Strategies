from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 24

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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Check existing position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit if RSI has rebounded above 55 or hold horizon exceeded
            if current_rsi > 55.0 or bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_normalized" if current_rsi > 55.0 else "max_hold_reached",
                        "rsi": current_rsi,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate to avoid entering during broad liquidity crises / meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("MELTDOWN", "CRISIS"):
            return None

        # Triple Confluence Edges
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Funding rate negative (crowd shorting)
        funding_edge = funding_rate < 0.00002
        # Fear & Greed below 38 (extreme fear / fear)
        sentiment_edge = fear_greed <= 38
        # Price oversold
        rsi_edge = current_rsi <= 36.0

        if funding_edge and sentiment_edge and rsi_edge:
            self.entry_bar = ctx.bar_index
            
            # Confidence scaled by setup intensity
            confidence = 0.65
            if funding_rate < -0.0001:
                confidence += 0.15
            if current_rsi < 28.0:
                confidence += 0.10
            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": current_rsi,
                    "funding_rate": funding_rate,
                    "fear_greed": fear_greed,
                    "crisis_score": crisis_score,
                }
            )

        return None