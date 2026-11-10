from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 24  # Max 24 hours hold

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        if len(closes) < self.rsi_period + 3:
            return None

        rsi_now = self._calc_rsi(closes)
        rsi_prev = self._calc_rsi(closes[:-1])
        if rsi_now is None or rsi_prev is None:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Normalization exits
            funding = ctx.features.get("funding_rate_ethusdt", 0.0)
            if rsi_now >= 55.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized", "rsi": rsi_now})
            if funding > 0.00015:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized", "funding": funding})
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_hold_reached", "bars_held": bars_held})
            
            return None

        # Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Triple edge conditions
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50)

        edge_funding = funding < 0.0
        edge_sentiment = fg_index < 35

        # Redesign: Strict event boundary via RSI bullish reversal hook out of oversold
        # Fires precisely when oversold conditions hit a turning inflection point
        event_trigger = (rsi_prev < 35.0) and (rsi_now > rsi_prev) and (rsi_now < 42.0)

        if edge_funding and edge_sentiment and event_trigger:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(funding) * 500.0 + (35 - fg_index) * 0.005)
            
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_oversold_inflection",
                    "rsi_now": round(rsi_now, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                    "close": ctx.bar.close,
                }
            )

        return None