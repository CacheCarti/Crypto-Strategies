from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GreedExhaustionShort(Strategy):
    METADATA = {
        "name": "Greed Exhaustion Short",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing for some mean reversion
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.ema_short_period = 5
        self.ema_long_period = 20
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

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Get Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate EMAs for momentum stall detection
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Momentum stall condition: short EMA flattening or crossing below long EMA
        # after a strong run (implied by high FGI)
        # We look for a recent peak and subsequent stall/downtick in the short EMA
        ema_short_prev = self._ema(closes[:-1], self.ema_short_period)
        if ema_short_prev is None:
            return None

        # Condition 1: High Greed
        greed_condition = fear_greed_index >= 75 # Threshold for "extreme greed" or high greed

        # Condition 2: Momentum Stall/Reversal
        # Check if short EMA has recently peaked and started to decline,
        # or if short EMA is about to cross or has just crossed below long EMA.
        # We need a clear signal of momentum exhaustion.
        momentum_stalling = False
        if ema_short < ema_long and ema_short_prev > ema_long: # Bearish cross
            momentum_stalling = True
        elif ema_short < ema_short_prev * 0.999 and ema_short > ema_long: # Short EMA is ticking down slightly but still above long
            # This captures the "stalling" aspect before a full cross
            # We need to ensure it's not just noise, so a slight decline is needed
            # and it should be after a period where EMA short was clearly rising
            # For simplicity, we'll focus on the cross for a clearer signal.
            pass


        # Entry logic for short position
        if not ctx.has_position():
            if greed_condition and momentum_stalling:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75, # High confidence for contrarian entry
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "greed_exhaustion_short_entry",
                        "fear_greed_index": fear_greed_index,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "price": current_close,
                    }
                )

        # Exit logic for open position
        if ctx.has_position() and ctx.position_direction() == "short":
            # Exit when fear_greed_index normalizes (below 50) or momentum resumes (short EMA crosses above long EMA)
            if fear_greed_index < 50 or ema_short > ema_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "greed_exhaustion_short_exit",
                        "fear_greed_index": fear_greed_index,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "price": current_close,
                        "exit_type": "normalized_greed" if fear_greed_index < 50 else "momentum_resumption"
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis
        # print(f"Position closed: {position}")
        pass