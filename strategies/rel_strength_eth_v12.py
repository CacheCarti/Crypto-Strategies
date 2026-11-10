from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold on 1h bars
        "warmup_bars": 55,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_threshold = 2.2  # 2.2% 24h spread divergence
        self.exit_threshold = 0.5   # Mean reversion threshold for spread
        self.trend_ema_period = 50

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature readings: 24h return spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Regime & Crisis Filter (avoid crisis breakdowns where cross-asset correlation spikes)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or regime in ["CRISIS", "MELTDOWN"]:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Price trend confirmation
        ema50 = self._ema(closes, self.trend_ema_period)
        if ema50 is None:
            return None
        current_close = ctx.bar.close

        # Position Management: Exit on mean-reversion of the spread
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread <= self.exit_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long", "spread": spread})
            elif direction == "short" and spread >= -self.exit_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short", "spread": spread})
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Strong divergence aligned with underlying price trend
        # Long ETH when ETH significantly outperforms BTC and is above its intermediate trend
        if spread >= self.entry_threshold and current_close > ema50:
            confidence = min(0.9, 0.55 + (spread - self.entry_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "close": current_close,
                    "ema50": ema50
                }
            )

        # Short ETH when ETH significantly underperforms BTC and is below its intermediate trend
        elif spread <= -self.entry_threshold and current_close < ema50:
            confidence = min(0.9, 0.55 + (abs(spread) - self.entry_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_drift",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "close": current_close,
                    "ema50": ema50
                }
            )

        return None