from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.2
        self.cooldown_period = 8
        self.last_exit_bar = -999
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
        if len(closes) < 35:
            return None

        # Features provide 24h rolling return %
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Local technical context
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None
        current_close = closes[-1]

        # 1. Manage Active Positions
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction")
            bars_held = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 0

            # Mean-reversion exit conditions: when spread snaps back towards neutrality
            if direction == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_rs_long_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif direction == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_rs_short_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # 2. Strict Entry Cooldown Gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid entries in crisis / high meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 3. High-Conviction Entries
        # LONG: ETH materially outperforming BTC (>2.2%) AND confirming local price action above EMA20
        if spread >= self.spread_threshold and current_close > ema20:
            conf = min(0.9, 0.6 + abs(spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_rs_momentum_breakout_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "close": current_close
                }
            )

        # SHORT: ETH materially lagging BTC (<-2.2%) AND confirming weakness below EMA20
        elif spread <= -self.spread_threshold and current_close < ema20:
            conf = min(0.9, 0.6 + abs(abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_rs_breakdown_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "close": current_close
                }
            )

        return None