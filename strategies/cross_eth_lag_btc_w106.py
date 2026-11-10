from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergence(Strategy):
    METADATA = {
        "name": "Beta Convergence BTC-ETH",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24 # for EMA and return comparison
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

        # Store ETH and BTC returns to calculate relative movement
        self.eth_returns_history = []
        self.btc_returns_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        self.eth_returns_history.append(eth_return_pct)
        self.btc_returns_history.append(btc_return_pct)

        # Keep history limited to what's needed for EMA
        if len(self.eth_returns_history) > self.lookback_period * 2:
            self.eth_returns_history.pop(0)
            self.btc_returns_history.pop(0)

        if len(self.eth_returns_history) < self.lookback_period:
            return None

        # Calculate EMA of recent returns to gauge "freshness" and trend
        eth_ema_return = self._ema(self.eth_returns_history, self.lookback_period)
        btc_ema_return = self._ema(self.btc_returns_history, self.lookback_period)

        if eth_ema_return is None or btc_ema_return is None:
            return None

        # Price-based condition: current BTC price relative to its short-term EMA
        btc_close = ctx.bar.close
        btc_ema_price = self._ema(ctx.closes(self.lookback_period), self.lookback_period)

        if btc_ema_price is None:
            return None

        # Check for existing position
        if ctx.has_position():
            # Exit condition: relationship normalizes (beta convergence)
            # Or if BTC significantly deviates from its EMA in the opposite direction
            if ctx.position_direction() == "long":
                # If ETH's recent performance cools down or BTC catches up
                if eth_ema_return < 0.005 or btc_ema_return > eth_ema_return * 0.8:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "beta_convergence_long_normalized",
                        "eth_ema_return": eth_ema_return,
                        "btc_ema_return": btc_ema_return,
                        "current_btc_price": btc_close,
                        "btc_ema_price": btc_ema_price
                    })
            elif ctx.position_direction() == "short":
                # If ETH's recent performance recovers or BTC catches down
                if eth_ema_return > -0.005 or btc_ema_return < eth_ema_return * 0.8:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "beta_convergence_short_normalized",
                        "eth_ema_return": eth_ema_return,
                        "btc_ema_return": btc_ema_return,
                        "current_btc_price": btc_close,
                        "btc_ema_price": btc_ema_price
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        # Long condition: ETH has made a fresh positive move, but BTC has not caught up
        if eth_ema_return > 0.01 and \
           btc_ema_return < eth_ema_return * 0.5 and \
           btc_close > btc_ema_price * 0.998: # BTC not too far below its own EMA
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "eth_leading_btc_lagging_long",
                "eth_ema_return": eth_ema_return,
                "btc_ema_return": btc_ema_return,
                "current_btc_price": btc_close,
                "btc_ema_price": btc_ema_price
            })

        # Short condition: ETH has made a fresh negative move, but BTC has not caught up
        if eth_ema_return < -0.01 and \
           btc_ema_return > eth_ema_return * 0.5 and \
           btc_close < btc_ema_price * 1.002: # BTC not too far above its own EMA
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "eth_leading_btc_lagging_short",
                "eth_ema_return": eth_ema_return,
                "btc_ema_return": btc_ema_return,
                "current_btc_price": btc_close,
                "btc_ema_price": btc_ema_price
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You can log position close details here or implement adaptive logic.
        pass