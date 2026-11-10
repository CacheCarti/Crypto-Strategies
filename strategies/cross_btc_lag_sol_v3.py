from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class BtcSolBetaConvergence(Strategy):
    METADATA = {
        "name": "BTC-SOL Beta Convergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Target 4-hour hold
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_window = 5 # Lookback for BTC return
        self.sol_window = 2 # Lookback for SOL return
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.btc_return_threshold = 0.005 # 0.5% move in BTC
        self.divergence_threshold = 0.002 # 0.2% divergence
        self.ema_period = 20 # For trend filtering
        self.price_history = collections.deque(maxlen=self.ema_period)
        self.ema = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        self.price_history.append(ctx.bar.close)
        self.ema = self._ema(list(self.price_history), self.ema_period)

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get BTC and SOL returns
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_window)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_window)]

        # Ensure we have enough data for returns
        if len(btc_returns) < self.btc_window or len(sol_returns) < self.sol_window:
            return None

        # Calculate recent average returns
        avg_btc_return = sum(btc_returns) / self.btc_window
        avg_sol_return = sum(sol_returns) / self.sol_window

        current_close = ctx.bar.close

        # Long condition: BTC made a significant positive move, but SOL hasn't caught up
        if avg_btc_return > self.btc_return_threshold and \
           (avg_btc_return - avg_sol_return) > self.divergence_threshold:
            
            # Additional filter: Price above EMA for bullish trend confirmation
            if self.ema is not None and current_close > self.ema:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75,
                                      metadata={"reason": "btc_lead_sol_lag_long",
                                                "avg_btc_return": avg_btc_return,
                                                "avg_sol_return": avg_sol_return,
                                                "divergence": avg_btc_return - avg_sol_return,
                                                "current_close": current_close,
                                                "ema": self.ema})
                elif ctx.position_direction() == "short":
                    # Flip from short to long if conditions are strong
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.8,
                                      metadata={"reason": "flip_short_to_long_btc_lead_sol_lag",
                                                "avg_btc_return": avg_btc_return,
                                                "avg_sol_return": avg_sol_return,
                                                "divergence": avg_btc_return - avg_sol_return,
                                                "current_close": current_close,
                                                "ema": self.ema})

        # Short condition: BTC made a significant negative move, but SOL hasn't caught up (or even moved up)
        elif avg_btc_return < -self.btc_return_threshold and \
             (avg_sol_return - avg_btc_return) > self.divergence_threshold:

            # Additional filter: Price below EMA for bearish trend confirmation
            if self.ema is not None and current_close < self.ema:
                if not ctx.has_position():
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75,
                                      metadata={"reason": "btc_lead_sol_lag_short",
                                                "avg_btc_return": avg_btc_return,
                                                "avg_sol_return": avg_sol_return,
                                                "divergence": avg_sol_return - avg_btc_return,
                                                "current_close": current_close,
                                                "ema": self.ema})
                elif ctx.position_direction() == "long":
                    # Flip from long to short if conditions are strong
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.8,
                                      metadata={"reason": "flip_long_to_short_btc_lead_sol_lag",
                                                "avg_btc_return": avg_btc_return,
                                                "avg_sol_return": avg_sol_return,
                                                "divergence": avg_sol_return - avg_btc_return,
                                                "current_close": current_close,
                                                "ema": self.ema})

        # Exit condition: If in a position and the divergence has normalized or reversed
        if ctx.has_position():
            if ctx.position_direction() == "long" and (avg_btc_return < self.btc_return_threshold / 2 or (avg_sol_return - avg_btc_return) > 0):
                # Exit long if BTC momentum fades or SOL starts to outperform BTC
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6,
                                  metadata={"reason": "btc_sol_convergence_long_exit",
                                            "avg_btc_return": avg_btc_return,
                                            "avg_sol_return": avg_sol_return,
                                            "current_close": current_close})
            elif ctx.position_direction() == "short" and (avg_btc_return > -self.btc_return_threshold / 2 or (avg_btc_return - avg_sol_return) > 0):
                # Exit short if BTC momentum fades or BTC starts to outperform SOL
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6,
                                  metadata={"reason": "btc_sol_convergence_short_exit",
                                            "avg_btc_return": avg_btc_return,
                                            "avg_sol_return": avg_sol_return,
                                            "current_close": current_close})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this exploration, just log
        pass