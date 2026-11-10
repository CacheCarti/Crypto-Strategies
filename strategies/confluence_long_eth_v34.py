from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.entry_rsi_max = 38.0
        self.fear_greed_max = 40.0
        self.funding_rate_max = 0.00002
        self.exit_rsi = 55.0
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar_idx = -999

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.rsi_period + 2:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if rsi >= self.exit_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized", "rsi": rsi, "bars_held": bars_held})
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_stop_reached", "rsi": rsi, "bars_held": bars_held})
            if funding > 0.0003:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.65, metadata={"reason": "funding_crowded_long", "funding": funding, "bars_held": bars_held})
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Confluence stack
        cond_funding = funding <= self.funding_rate_max
        cond_fg = fg_index <= self.fear_greed_max
        cond_rsi = rsi <= self.entry_rsi_max

        if cond_funding and cond_fg and cond_rsi:
            self.entry_bar_idx = ctx.bar_index
            depth_score = min(1.0, max(0.0, (self.entry_rsi_max - rsi) / 15.0))
            conf = 0.65 + 0.25 * depth_score
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fg_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index