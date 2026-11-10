from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceOversoldSwing(Strategy):
    METADATA = {
        "name": "EthConfluenceOversoldSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = 0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        has_pos = ctx.has_position()

        # Handle active position management and dynamic normalization exit
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            # Exit if confluence normalized (mean reversion achieved) or max duration reached
            if (rsi >= 50.0 and funding >= 0.00005) or rsi >= 60.0 or bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_time_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "bars_held": bars_held,
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entering during systemic meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Triple-confluence logic:
        # 1. Price oversold (RSI <= 38.0 allows robust capture without being hyper-fragile)
        # 2. Crowd positioning negative or discounted funding (<= 0.00002)
        # 3. Macro sentiment fear-gated (fear_greed <= 40.0)
        cond_rsi = rsi <= 38.0
        cond_funding = funding <= 0.00002
        cond_fg = fear_greed <= 40.0

        if cond_rsi and cond_funding and cond_fg:
            # Scale confidence by depth of confluence
            conf = 0.65
            if funding < 0.0:
                conf += 0.1
            if rsi < 30.0:
                conf += 0.15
            conf = min(conf, 0.95)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "triple_confluence_oversold_entry",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fear_greed,
                    "entry_price": ctx.bar.close,
                    "regime": market_regime,
                },
            )

        return None