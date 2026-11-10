from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # 1 hour bars, aiming for 1-2 day hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_short = 10
        self.ema_period_long = 30
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # State to detect transitions
        self.prev_btc_trend_bullish = False
        self.prev_eth_trend_bullish = False
        self.prev_btc_trend_bearish = False
        self.prev_eth_trend_bearish = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown period after a previous signal
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate EMAs for ETH
        ema_short = self._ema(closes, self.ema_period_short)
        ema_long = self._ema(closes, self.ema_period_long)

        if ema_short is None or ema_long is None:
            return None

        current_price = ctx.bar.close

        # Get BTC and ETH returns (24h returns, from features)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Define trend conditions based on returns and EMA cross
        btc_trend_bullish = btc_return_pct > 0.05  # BTC showing positive momentum
        eth_trend_bullish = eth_return_pct > 0.05 and current_price > ema_short and ema_short > ema_long # ETH positive momentum and bullish EMA cross

        btc_trend_bearish = btc_return_pct < -0.05 # BTC showing negative momentum
        eth_trend_bearish = eth_return_pct < -0.05 and current_price < ema_short and ema_short < ema_long # ETH negative momentum and bearish EMA cross

        # Check for position close conditions
        if ctx.has_position():
            position = ctx.position
            # Exit if BTC trend reverses or ETH trend normalizes (EMAs flatten/cross against)
            if position["direction"] == "long":
                # Exit if BTC turns bearish or ETH EMAs cross bearishly
                if btc_trend_bearish or (current_price < ema_short and ema_short < ema_long):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_btc_bearish_or_eth_ema_flip",
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "current_price": current_price,
                        "ema_short": ema_short,
                        "ema_long": ema_long
                    })
            elif position["direction"] == "short":
                # Exit if BTC turns bullish or ETH EMAs cross bullishly
                if btc_trend_bullish or (current_price > ema_short and ema_short > ema_long):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_btc_bullish_or_eth_ema_flip",
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "current_price": current_price,
                        "ema_short": ema_short,
                        "ema_long": ema_long
                    })
            return None # Hold existing position if no exit condition met

        # Entry logic
        # Long entry: BTC bullish, ETH bullish, and a fresh cross/confirmation
        if btc_trend_bullish and eth_trend_bullish and not self.prev_btc_trend_bullish and not self.prev_eth_trend_bullish:
            self.last_signal_bar = ctx.bar_index
            self.prev_btc_trend_bullish = btc_trend_bullish
            self.prev_eth_trend_bullish = eth_trend_bullish
            self.prev_btc_trend_bearish = btc_trend_bearish
            self.prev_eth_trend_bearish = eth_trend_bearish
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "cross_asset_bullish_confirmation",
                "btc_return_pct": btc_return_pct,
                "eth_return_pct": eth_return_pct,
                "current_price": current_price,
                "ema_short": ema_short,
                "ema_long": ema_long
            })

        # Short entry: BTC bearish, ETH bearish, and a fresh cross/confirmation
        if btc_trend_bearish and eth_trend_bearish and not self.prev_btc_trend_bearish and not self.prev_eth_trend_bearish:
            self.last_signal_bar = ctx.bar_index
            self.prev_btc_trend_bullish = btc_trend_bullish
            self.prev_eth_trend_bullish = eth_trend_bullish
            self.prev_btc_trend_bearish = btc_trend_bearish
            self.prev_eth_trend_bearish = eth_trend_bearish
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "cross_asset_bearish_confirmation",
                "btc_return_pct": btc_return_pct,
                "eth_return_pct": eth_return_pct,
                "current_price": current_price,
                "ema_short": ema_short,
                "ema_long": ema_long
            })

        # Update previous state for transition detection
        self.prev_btc_trend_bullish = btc_trend_bullish
        self.prev_eth_trend_bullish = eth_trend_bullish
        self.prev_btc_trend_bearish = btc_trend_bearish
        self.prev_eth_trend_bearish = eth_trend_bearish

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this strategy, just logging.
        pass