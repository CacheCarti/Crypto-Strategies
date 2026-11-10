from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.ema_period = 30
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
        closes = ctx.closes(self.trend_ema_period + 5)
        if len(closes) < self.trend_ema_period:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "crisis_exit", "regime": market_regime, "spread": spread}
                )
            return None

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ema_short = self._ema(closes, self.ema_period)
        ema_long = self._ema(closes, self.trend_ema_period)
        if ema_short is None or ema_long is None:
            return None

        current_price = ctx.bar.close
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if spread > self.spread_threshold and current_price > ema_long and fg_index > 25.0:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_momentum_outperformance",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_long": round(ema_long, 2),
                    "price": round(current_price, 2),
                    "fg_index": fg_index
                }
            )

        if spread < -self.spread_threshold and current_price < ema_long and fg_index < 75.0:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_momentum_underperformance",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_long": round(ema_long, 2),
                    "price": round(current_price, 2),
                    "fg_index": fg_index
                }
            )

        return None