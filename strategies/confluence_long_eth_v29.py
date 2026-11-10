from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.last_exit_bar = -999
        self.cooldown_bars = 10
        self.entry_bar = -1

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.rsi_period + 2:
            return None

        rsi_current = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)
        if rsi_current is None or rsi_prev is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalize exit: crowd stops shorting or RSI normalizes above 50 or time stop
            if rsi_current >= 52.0 or funding >= 0.0001 or bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_time",
                        "rsi": round(rsi_current, 2),
                        "funding": round(funding, 6),
                        "fear_greed": round(fear_greed, 1),
                        "bars_held": bars_held,
                    },
                )
            return None

        # Mandatory cooldown to space event boundaries
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Event trigger: Triple confluence with dynamic RSI cross/reversal to avoid holding knives
        # Negative funding (crowd short), Fear & Greed <= 38, RSI <= 38 with a stabilization turn
        crowd_short = funding <= 0.0
        extreme_sentiment = fear_greed <= 38.0
        oversold_trigger = (rsi_current <= 36.0 and rsi_current >= rsi_prev) or (rsi_prev < 32.0 and rsi_current >= 32.0)

        if crowd_short and extreme_sentiment and oversold_trigger:
            self.entry_bar = ctx.bar_index
            confidence = 0.70
            if funding < -0.00005:
                confidence += 0.15
            if fear_greed < 25.0:
                confidence += 0.10
            confidence = min(1.0, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi_current, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fear_greed, 1),
                    "price": ctx.bar.close,
                },
            )

        return None