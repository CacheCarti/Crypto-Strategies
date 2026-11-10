from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRelativeStrengthCross(Strategy):
    METADATA = {
        "name": "EthRelativeStrengthCross",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% threshold
        self.cooldown_bars = 8       # minimum bars between trades to prevent churn
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

        # Fetch returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Base technical filter: EMA trend to align price action with momentum
        ema20 = self._ema(closes, 20)
        current_price = ctx.bar.close

        # Position management: Mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # If spread compresses back toward equilibrium, exit with profit/neutral
            if direction == "long" and spread < 0.25:
                self.prev_spread = spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_mean_reverted", "spread": spread, "prev_spread": self.prev_spread}
                )
            elif direction == "short" and spread > -0.25:
                self.prev_spread = spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_mean_reverted", "spread": spread, "prev_spread": self.prev_spread}
                )
            self.prev_spread = spread
            return None

        # Cooldown guard: Avoid immediate re-entries and fee churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Event-boundary entry: strict threshold breakout/cross, not persistent level
        # This prevents clustering and multiple entries on prolonged outperformance
        long_cross = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        short_cross = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)

        self.prev_spread = spread

        # Market regime filter to avoid trading during absolute meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long: ETH breaks out above BTC by 2% AND price confirms above short-term EMA
        if long_cross and (ema20 is not None and current_price > ema20):
            conf = min(0.9, 0.6 + abs(spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_relative_strength_cross_bull",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": current_price,
                }
            )

        # Short: ETH breaks down below BTC by -2% AND price confirms below short-term EMA
        if short_cross and (ema20 is not None and current_price < ema20):
            conf = min(0.9, 0.6 + abs(spread - (-self.spread_threshold)) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_relative_strength_cross_bear",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": current_price,
                }
            )

        return None