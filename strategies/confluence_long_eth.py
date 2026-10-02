from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluence(Strategy):
    METADATA = {
        "name": "Triple Edge Confluence",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.rsi_entry_thresh = 35.0
        self.rsi_exit_thresh = 55.0
        self.fg_entry_thresh = 38.0
        self.fg_exit_thresh = 50.0
        self.funding_exit_thresh = 0.0001
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _rsi(self, closes, period=14) -> Optional[float]:
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
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        has_pos = ctx.has_position()

        # Exit management: normalize if sentiment, funding, or RSI recovers
        if has_pos:
            exit_reason = None
            if rsi >= self.rsi_exit_thresh:
                exit_reason = "rsi_normalized"
            elif fear_greed >= self.fg_exit_thresh:
                exit_reason = "fear_greed_normalized"
            elif funding >= self.funding_exit_thresh:
                exit_reason = "funding_normalized"

            if exit_reason:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "fear_greed": fear_greed,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Entry gate: Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Triple-stack conditions:
        # 1. RSI oversold
        # 2. Fear & Greed in fear zone
        # 3. Perp funding negative (crowd shorting)
        is_rsi_oversold = rsi <= self.rsi_entry_thresh
        is_market_fear = fear_greed <= self.fg_entry_thresh
        is_crowd_short = funding <= 0.0

        if is_rsi_oversold and is_market_fear and is_crowd_short:
            # Scale confidence based on extremity of oversold & fear
            conf = 0.65 + min(0.30, (self.rsi_entry_thresh - rsi) * 0.01 + max(0.0, -funding * 500))
            conf = min(0.95, max(0.5, conf))

            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_confluence_entry",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fear_greed,
                    "close": ctx.bar.close,
                },
            )

        return None