from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        has_pos = ctx.has_position()

        # Exit logic for open positions
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            exit_reason = None

            # Normalization exits or time-based cutoff
            if rsi >= 54.0:
                exit_reason = "rsi_normalized"
            elif funding > 0.0001:
                exit_reason = "funding_turned_crowded_long"
            elif bars_held >= self.max_hold_bars:
                exit_reason = "time_horizon_reached"

            if exit_reason:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Entry logic: enforcing strict post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Triple edge condition: crowd short (funding <= 0), sentiment fear (FG < 38), oversold price (RSI < 38)
        # Using 38 threshold prevents over-filtering while strictly preserving the triple-stack thesis
        if funding <= 0.0 and fear_greed < 38.0 and rsi < 38.0:
            # Scaled conviction based on depth of oversold/fear
            confidence = min(0.9, 0.55 + (38.0 - rsi) * 0.015 + max(0.0, -funding * 500.0))
            self.entry_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_confluence_oversold_fear_negfunding",
                    "rsi": round(rsi, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fear_greed, 1),
                    "close": ctx.bar.close,
                },
            )

        return None