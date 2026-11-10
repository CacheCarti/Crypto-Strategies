from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluence(Strategy):
    METADATA = {
        "name": "Triple Edge Confluence",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,  # 12 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 16          # 16-bar cooldown to prevent clustering
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.max_hold_bars = 24          # 24h max hold on 1h bars

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 3)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi_curr = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)
        if rsi_curr is None or rsi_prev is None:
            return None

        # Manage open position
        if ctx.has_position():
            self.bars_in_trade += 1
            # Normalization exit or max hold time boundary
            if rsi_curr >= 50.0 or self.bars_in_trade >= self.max_hold_bars:
                reason = "rsi_normalized" if rsi_curr >= 50.0 else "max_hold_reached"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": reason, "rsi": round(rsi_curr, 2), "bars_held": self.bars_in_trade}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Macro/Crowd Filters
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Regimes: Crowd heavily shorting and general market fear
        is_fear = fear_greed <= 38.0
        is_crowd_short = funding_rate <= 0.00002

        # Event trigger: strict upward momentum turn from oversold boundary
        # Triggering on RSI crossing back above 32 after dipping into deep oversold
        rsi_oversold_cross = (rsi_prev <= 34.0) and (rsi_curr > rsi_prev) and (rsi_curr <= 40.0)

        if is_fear and is_crowd_short and rsi_oversold_cross:
            # Scaled confidence based on extremity of crowd shorting and fear
            confidence = 0.65
            if funding_rate < 0.0:
                confidence += 0.15
            if fear_greed < 25.0:
                confidence += 0.10
            confidence = min(1.0, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "triple_edge_oversold_turn",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "fear_greed": fear_greed,
                    "funding_rate": funding_rate
                }
            )

        return None