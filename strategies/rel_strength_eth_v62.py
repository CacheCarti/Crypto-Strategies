from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.max_hold_bars = 24

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        # Market regime gating to address holdout failure: avoid crisis/meltdown
        mkt_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if mkt_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_risk_exit", "regime": mkt_regime, "crisis_score": crisis_score}
                )
            return None

        # Cross-asset relative performance spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Local trend confirmation via EMA
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None
        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", price) if ctx.position else price
            
            # Mean-reversion exit on spread normalization
            if pos_dir == "long" and spread <= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_long", "spread": spread, "price": price}
                )
            elif pos_dir == "short" and spread >= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_short", "spread": spread, "price": price}
                )
            return None

        # Check cooldown to strictly control trade frequency
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Sentiment regime filter
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Long: ETH outperforming BTC by >2%, price above EMA20, sentiment not extreme panic
        if spread >= self.spread_threshold and price > ema20 and fear_greed >= 30:
            conf = min(0.9, 0.5 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "fear_greed": fear_greed
                }
            )

        # Short: ETH underperforming BTC by <-2%, price below EMA20, sentiment not extreme euphoria
        if spread <= -self.spread_threshold and price < ema20 and fear_greed <= 75:
            conf = min(0.9, 0.5 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "fear_greed": fear_greed
                }
            )

        return None