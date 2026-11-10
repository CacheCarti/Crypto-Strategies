from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold for swing 1h bars
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% divergence threshold
        self.reversion_threshold = 0.5  # Mean reversion exit threshold
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _ema(self, values, period: int):
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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Feature extraction
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Price-trend confirmation using EMA
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        price = ctx.bar.close

        # Position Management & Dynamic Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit condition
            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_spread_reverted",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": price
                })
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_spread_reverted",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": price
                })
            return None

        # Mandatory cooldown check after last trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out crisis regime to avoid toxic spread expansions
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: ETH leadership vs BTC combined with local trend confirmation
        if spread >= self.spread_threshold and price > ema_fast > ema_slow:
            # Scaled confidence based on spread strength
            confidence = min(0.85, 0.60 + min(abs(spread - self.spread_threshold) * 0.05, 0.25))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_relative_strength_expansion_long",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": price
                }
            )

        if spread <= -self.spread_threshold and price < ema_fast < ema_slow:
            confidence = min(0.85, 0.60 + min(abs(abs(spread) - self.spread_threshold) * 0.05, 0.25))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_relative_weakness_expansion_short",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": price
                }
            )

        return None