from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LeaderConfirmationMomentum(Strategy):
    METADATA = {
        "name": "Leader Confirmation Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.ema_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        eth_momentum = self._momentum(closes, self.momentum_period)
        eth_ema = self._ema(closes, self.ema_period)

        if eth_momentum is None or eth_ema is None:
            return None

        # BTC volatility check: avoid trading during extreme BTC volatility (proxy for crisis spike)
        # Using a simple threshold on absolute BTC return percentage
        btc_volatility_threshold = 0.5 # e.g., 0.5% in an hour is significant
        if abs(btc_returns) > btc_volatility_threshold:
            return None # Skip trading if BTC is too volatile

        if not ctx.has_position():
            # Long entry conditions
            if eth_momentum > 0 and closes[-1] > eth_ema and btc_returns > 0:
                # ETH momentum up, price above EMA, and BTC also positive
                confidence = min(0.7, abs(eth_momentum) / (closes[-1] * 0.01)) # Scale confidence by momentum strength
                if confidence < 0.3:
                    return None
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": "eth_momentum_bullish_btc_confirms",
                    "eth_momentum": eth_momentum,
                    "eth_ema": eth_ema,
                    "btc_return_pct": btc_returns,
                    "close": closes[-1]
                })

            # Short entry conditions
            elif eth_momentum < 0 and closes[-1] < eth_ema and btc_returns < 0:
                # ETH momentum down, price below EMA, and BTC also negative
                confidence = min(0.7, abs(eth_momentum) / (closes[-1] * 0.01)) # Scale confidence by momentum strength
                if confidence < 0.3:
                    return None
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": "eth_momentum_bearish_btc_confirms",
                    "eth_momentum": eth_momentum,
                    "eth_ema": eth_ema,
                    "btc_return_pct": btc_returns,
                    "close": closes[-1]
                })
        else:
            # Exit conditions (momentum exhaustion or BTC confirmation loss)
            current_direction = ctx.position_direction()
            exit_confidence = 0.6

            if current_direction == "long":
                # Exit long if ETH momentum turns negative or BTC returns turn negative
                if eth_momentum < 0 or btc_returns < -0.1: # Small negative BTC return can trigger exit
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=exit_confidence, metadata={
                        "reason": "long_exit_momentum_exhaustion_or_btc_loss_confirmation",
                        "eth_momentum": eth_momentum,
                        "btc_return_pct": btc_returns,
                        "close": closes[-1]
                    })
            elif current_direction == "short":
                # Exit short if ETH momentum turns positive or BTC returns turn positive
                if eth_momentum > 0 or btc_returns > 0.1: # Small positive BTC return can trigger exit
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=exit_confidence, metadata={
                        "reason": "short_exit_momentum_exhaustion_or_btc_loss_confirmation",
                        "eth_momentum": eth_momentum,
                        "btc_return_pct": btc_returns,
                        "close": closes[-1]
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Use for logging or adaptive sizing if needed.
        # For simplicity, we just log here.
        pass