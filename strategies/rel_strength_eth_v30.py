from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% divergence
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.prev_spread = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Feature divergence calculation
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Local price-action confirmation (20-period EMA trend filter)
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None
        current_close = ctx.bar.close

        # Manage open position: exit if spread mean-reverts through 0
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread <= 0.0:
                self.prev_spread = spread
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_spread_reverted",
                    "spread": spread,
                    "close": current_close,
                })
            elif direction == "short" and spread >= 0.0:
                self.prev_spread = spread
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_spread_reverted",
                    "spread": spread,
                    "close": current_close,
                })
            self.prev_spread = spread
            return None

        # Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Strict event boundary: crossing threshold from below/above
        # Cross above +2.0% spread while price supports trend
        if self.prev_spread <= self.spread_threshold < spread:
            if current_close > ema20:
                self.prev_spread = spread
                return ctx.signal("long", confidence=0.8, stop_loss_bps=280.0, take_profit_bps=560.0, metadata={
                    "reason": "eth_momentum_breakout_cross_up",
                    "spread": spread,
                    "prev_spread": self.prev_spread,
                    "ema20": ema20,
                    "close": current_close
                })

        # Cross below -2.0% spread while price confirms downtrend
        elif self.prev_spread >= -self.spread_threshold > spread:
            if current_close < ema20:
                self.prev_spread = spread
                return ctx.signal("short", confidence=0.8, stop_loss_bps=280.0, take_profit_bps=560.0, metadata={
                    "reason": "eth_momentum_breakdown_cross_down",
                    "spread": spread,
                    "prev_spread": self.prev_spread,
                    "ema20": ema20,
                    "close": current_close
                })

        self.prev_spread = spread
        return None