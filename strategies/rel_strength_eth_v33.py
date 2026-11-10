from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 1.8
        self.reversion_threshold = 0.4
        self.ema_period = 24
        self.max_hold_bars = 16

    def _ema(self, values: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.ema_period + 5)
        if len(closes) < self.ema_period:
            return None

        # Pull backtestable return features
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Price confirmation filter: short-term trend direction via EMA
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")

        # In severe crisis or meltdown, step aside
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Active position management: exit when relative strength mean-reverts
        if ctx.has_position():
            direction = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", price)

            # Reversion exit
            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "eth_lead_faded", "spread": spread, "price": price})
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "eth_lag_reverted", "spread": spread, "price": price})
            return None

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long: ETH strongly outperforming BTC and price above EMA (momentum aligned)
        if spread >= self.spread_threshold and price > ema_val:
            conf = min(0.85, 0.55 + min(abs(spread) / 10.0, 0.3))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_strength_breakout",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema": round(ema_val, 2),
                    "price": round(price, 2)
                }
            )

        # Short: ETH lagging BTC significantly and price below EMA (weakness continuation)
        elif spread <= -self.spread_threshold and price < ema_val:
            conf = min(0.85, 0.55 + min(abs(spread) / 10.0, 0.3))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_weakness_breakdown",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema": round(ema_val, 2),
                    "price": round(price, 2)
                }
            )

        return None