from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetEthTrend(Strategy):
    METADATA = {
        "name": "CrossAssetEthTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 50,  # Need enough bars for EMA and recent returns
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.momentum_period = 3
        self.cooldown_bars = 5  # Wait 5 bars after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + 1)
        if len(closes) < self.long_ema_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate EMAs
        short_ema = self._ema(closes, self.short_ema_period)
        long_ema = self._ema(closes, self.long_ema_period)

        if short_ema is None or long_ema is None:
            return None

        # BTC and ETH 24h returns
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        eth_return = ctx.features.get("eth_return_pct", 0.0)

        # BTC and ETH short-term momentum (using last N close changes)
        btc_momentum = ctx.features.get("btc_return_pct", 0.0) # Using the 24h return as a proxy for leader's "freshness"
        eth_momentum = eth_return # Using the 24h return as a proxy for target's "freshness"

        current_price = ctx.bar.close

        # Entry conditions
        if not ctx.has_position():
            # Long entry: BTC showing positive momentum and ETH confirming with a bullish EMA crossover
            if btc_momentum > 0.1 and eth_momentum > 0.1: # BTC positive, ETH positive
                if short_ema > long_ema and closes[-2] <= long_ema: # Recent bullish crossover
                    # Check for "freshness" - was it a recent cross, not a stale one?
                    if closes[-1] > long_ema and closes[-2] <= long_ema:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "btc_leader_eth_confirm_ema_cross_long",
                                "btc_return_pct": btc_return,
                                "eth_return_pct": eth_return,
                                "short_ema": short_ema,
                                "long_ema": long_ema,
                                "price": current_price,
                            }
                        )

            # Short entry: BTC showing negative momentum and ETH confirming with a bearish EMA crossover
            elif btc_momentum < -0.1 and eth_momentum < -0.1: # BTC negative, ETH negative
                if short_ema < long_ema and closes[-2] >= long_ema: # Recent bearish crossover
                    # Check for "freshness" - was it a recent cross, not a stale one?
                    if closes[-1] < long_ema and closes[-2] >= long_ema:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "btc_leader_eth_confirm_ema_cross_short",
                                "btc_return_pct": btc_return,
                                "eth_return_pct": eth_return,
                                "short_ema": short_ema,
                                "long_ema": long_ema,
                                "price": current_price,
                            }
                        )
        else:
            # Exit conditions
            # Close if BTC leader disagrees with our position (reverses direction)
            # Or if ETH EMA crossover reverses (trend normalization)
            if ctx.position_direction() == "long":
                if btc_momentum < -0.05 or short_ema < long_ema: # BTC turns negative, or ETH trend reverses
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_btc_disagree_or_eth_trend_normalize",
                            "btc_return_pct": btc_return,
                            "eth_return_pct": eth_return,
                            "short_ema": short_ema,
                            "long_ema": long_ema,
                            "price": current_price,
                        }
                    )
            elif ctx.position_direction() == "short":
                if btc_momentum > 0.05 or short_ema > long_ema: # BTC turns positive, or ETH trend reverses
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_btc_disagree_or_eth_trend_normalize",
                            "btc_return_pct": btc_return,
                            "eth_return_pct": eth_return,
                            "short_ema": short_ema,
                            "long_ema": long_ema,
                            "price": current_price,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already handle cooldown in on_bar, no specific action here for this strategy.
        pass