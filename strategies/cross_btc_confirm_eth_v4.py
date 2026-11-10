from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # ~3 hours average hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 7
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars  # Initialize to allow trading immediately

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
        if len(closes) < self.long_ema_period:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate EMAs for ETH
        short_ema_eth = self._ema(closes, self.short_ema_period)
        long_ema_eth = self._ema(closes, self.long_ema_period)

        if short_ema_eth is None or long_ema_eth is None:
            return None

        # Get BTC and ETH returns
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Market regime filtering
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Stand down in crisis or if trend is unclear/against
        if regime == "CRISIS" or regime == "MELTDOWN":
            return None
        if trend_regime == "neutral": # Only trade with a clear trend in the market
             return None

        # Entry conditions
        long_condition = False
        short_condition = False

        # BTC is the leader, ETH is the follower
        # Long: BTC shows strong positive return, ETH EMAs bullish cross, and ETH also positive
        if btc_return_pct > 0.5 and eth_return_pct > 0.3: # BTC up >0.5%, ETH up >0.3%
            if short_ema_eth > long_ema_eth and closes[-1] > short_ema_eth:
                long_condition = True

        # Short: BTC shows strong negative return, ETH EMAs bearish cross, and ETH also negative
        if btc_return_pct < -0.5 and eth_return_pct < -0.3: # BTC down <-0.5%, ETH down <-0.3%
            if short_ema_eth < long_ema_eth and closes[-1] < short_ema_eth:
                short_condition = True

        # Signal Generation
        if not ctx.has_position():
            if long_condition:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_btc_eth_bull_confirm",
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "eth_short_ema": short_ema_eth,
                        "eth_long_ema": long_ema_eth,
                        "current_price": closes[-1],
                        "market_regime": regime,
                        "trend_regime": trend_regime,
                    },
                )
            elif short_condition:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_btc_eth_bear_confirm",
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "eth_short_ema": short_ema_eth,
                        "eth_long_ema": long_ema_eth,
                        "current_price": closes[-1],
                        "market_regime": regime,
                        "trend_regime": trend_regime,
                    },
                )
        else:
            # Exit when the relationship normalizes or reverses
            if ctx.position_direction() == "long":
                # Exit if BTC return turns negative or ETH EMA crosses bearish
                if btc_return_pct < -0.1 or short_ema_eth < long_ema_eth:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_btc_or_eth_trend_reversal",
                            "btc_return_pct": btc_return_pct,
                            "eth_short_ema": short_ema_eth,
                            "eth_long_ema": long_ema_eth,
                            "current_price": closes[-1],
                        },
                    )
            elif ctx.position_direction() == "short":
                # Exit if BTC return turns positive or ETH EMA crosses bullish
                if btc_return_pct > 0.1 or short_ema_eth > long_ema_eth:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_btc_or_eth_trend_reversal",
                            "btc_return_pct": btc_return_pct,
                            "eth_short_ema": short_ema_eth,
                            "eth_long_ema": long_ema_eth,
                            "current_price": closes[-1],
                        },
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive sizing or state reset needed for this strategy
        pass