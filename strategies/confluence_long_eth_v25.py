from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.bars_in_trade = 0
        self.max_hold_bars = 18

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
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            # Normalization exit triggers or time-based exhaustion
            if rsi > 52.0 or funding > 0.0001 or self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_timeout",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "bars_held": self.bars_in_trade,
                    },
                )
            return None

        # Entry gating: Mandatory cooldown and crisis protection
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset relative resilience confirmation (ETH capitulating into support while BTC stabilizes)
        # Avoids prior family pitfall: catches falling knives during systemic collapses
        cross_asset_ok = btc_ret > -3.5

        # Three core confluence legs + cross-asset confirmation
        is_crowd_short = funding <= 0.0
        is_market_fear = fear_greed < 36.0
        is_oversold = rsi < 36.0

        if is_crowd_short and is_market_fear and is_oversold and cross_asset_ok:
            # Scaled confidence based on depth of oversold/negative funding
            base_conf = 0.65
            if funding < -0.00005:
                base_conf += 0.15
            if rsi < 30.0:
                base_conf += 0.10
            conf = min(base_conf, 0.95)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fear_greed,
                    "btc_24h_ret": round(btc_ret, 2),
                },
            )

        return None