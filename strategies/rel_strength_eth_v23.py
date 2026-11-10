from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% spread trigger
        self.ema_period = 20

    def _ema(self, values, period: int) -> Optional[float]:
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
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: enforce rest between positions to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        price_ema = self._ema(closes, self.ema_period)
        if price_ema is None:
            return None

        current_price = ctx.bar.close

        # Position management & mean reversion exit check
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of the spread triggers clean exit
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Filter out extreme crisis regimes to avoid single-tail blowouts
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Entry: ETH outperforming BTC by > threshold, confirmed by price above local EMA
        if spread >= self.spread_threshold and current_price > price_ema:
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_expansion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": price_ema,
                    "price": current_price
                }
            )

        # Short Entry: ETH underperforming BTC by < -threshold, confirmed by price below local EMA
        if spread <= -self.spread_threshold and current_price < price_ema:
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_expansion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": price_ema,
                    "price": current_price
                }
            )

        return None