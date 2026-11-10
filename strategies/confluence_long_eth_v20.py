from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthTripleConfluenceLong(Strategy):
    METADATA = {
        "name": "EthTripleConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Feature readings
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position management / exits
        if ctx.has_position():
            pos = ctx.position
            # Track hold time or state normalization
            bars_held = 0
            if pos and "entry_price" in pos:
                # Normalization of conditions or momentum recovery signals profit taking / rebalancing
                if rsi > 55.0 or funding_rate > 0.00015:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "edge_normalization_exit",
                            "rsi": round(rsi, 2),
                            "funding_rate": funding_rate,
                            "fear_greed": fg_index,
                        },
                    )
            return None

        # Enforce cooldown after prior trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Three independent edges:
        # 1. Market positioning: Negative funding indicating crowd is heavily/persistently shorting
        edge_crowd_short = funding_rate < 0.0

        # 2. Macro sentiment: Fear state (Fear & Greed <= 38 to allow robust state coverage)
        edge_macro_fear = fg_index <= 38.0

        # 3. Technical oversold / stretch: RSI < 38
        edge_tech_oversold = rsi < 38.0

        # All 3 legs must confluence
        if edge_crowd_short and edge_macro_fear and edge_tech_oversold:
            # Scale confidence by depth of confluence
            conf = 0.65
            if rsi < 30.0:
                conf += 0.15
            if funding_rate < -0.00005:
                conf += 0.10
            if fg_index < 25.0:
                conf += 0.10
            conf = min(0.95, conf)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "triple_confluence_funding_fg_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "fear_greed": fg_index,
                    "confidence": round(conf, 2),
                },
            )

        return None