from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_entry_threshold = 1.0
        self.spread_exit_threshold = 0.2
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.ema_period = 20

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

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        current_price = ctx.bar.close

        # Position management: mean-reversion exit when spread neutralizes
        if ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long" and spread <= self.spread_exit_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_relative_strength_reverted",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "price": current_price
                    }
                )
            elif direction == "short" and spread >= -self.spread_exit_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_relative_weakness_reverted",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "price": current_price
                    }
                )
            return None

        # Entry logic: enforce cooldown after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup: ETH outperforming BTC by >= 1.0% with price above EMA support
        if spread >= self.spread_entry_threshold and current_price >= ema * 0.995:
            confidence = min(0.85, 0.55 + (spread / 8.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_lead_long",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema, 2),
                    "price": current_price
                }
            )

        # Short Setup: ETH underperforming BTC by <= -1.0% with price below EMA resistance
        if spread <= -self.spread_entry_threshold and current_price <= ema * 1.005:
            confidence = min(0.85, 0.55 + (abs(spread) / 8.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_lag_short",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema, 2),
                    "price": current_price
                }
            )

        return None