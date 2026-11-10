from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 24
        self.entry_bar = 0

    def _rsi(self, closes, period=14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalization exit conditions: RSI rebounds or time horizon reached
            if current_rsi >= 55.0 or bars_held >= self.max_hold_bars or funding > 0.0001:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "confluence_normalized_or_max_bars",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme engine meltdown / crisis to avoid falling knives
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Triple edge confluence: negative funding, fear, oversold RSI
        is_negative_funding = funding < 0.0
        is_fear = fg_index <= 35.0
        is_oversold = current_rsi <= 35.0

        if is_negative_funding and is_fear and is_oversold:
            # Scaled confidence based on depth of oversold & fear
            conf = min(0.9, 0.6 + max(0.0, (30.0 - current_rsi) * 0.01) + max(0.0, (30.0 - fg_index) * 0.005))
            self.entry_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_oversold",
                    "rsi": round(current_rsi, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "regime": regime
                }
            )

        return None