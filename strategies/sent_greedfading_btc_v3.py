from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeBTC(Strategy):
    METADATA = {
        "name": "Sentiment Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 60,  # Need enough bars for 50-period EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 50
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars  # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry condition: Short only
        # 1. Extreme Greed sentiment
        # 2. Fast EMA crosses below Slow EMA (momentum loss)
        # 3. Price is above slow EMA (still in an uptrend, but losing steam)
        
        if ctx.has_position():
            # If we are in a long position, and conditions for short apply, flip.
            # Otherwise, hold. We only fade rallies, so no long positions.
            if ctx.position_direction() == "long":
                # If we are long, and sentiment/momentum turn bearish, exit.
                if fear_greed_index >= 75 and fast_ema < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "exit_long_extreme_greed_momentum_loss",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "price": current_close
                    })
                return None # Hold existing long, no other exit conditions for long

        # No long entries in this strategy
        if ctx.position_direction() == "long":
            return None

        # Short entry logic
        # Check for Extreme Greed (index >= 75)
        # Check for fast EMA crossing below slow EMA (momentum loss, fade the rally)
        # Ensure price is still above the slow EMA, indicating a rally that is losing steam
        if (fear_greed_index >= 75 and
            fast_ema < slow_ema and
            ctx.closes(2)[-2] > ctx.closes(2)[-1] and # Price drop from previous bar
            current_close > slow_ema # Price is still above slow EMA
        ):
            # This is a high-conviction short signal
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_greed_fade_momentum_loss",
                    "fear_greed_index": fear_greed_index,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        pass