from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolGreedFade(Strategy):
    METADATA = {
        "name": "SolGreedFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 # No losses, infinite RSI
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + self.rsi_period + 1)
        if len(closes) < self.slow_ema_period + self.rsi_period + 1:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Strategy Logic: Fade rallies when Extreme Greed and momentum loss
        if not ctx.has_position():
            # Condition 1: Extreme Greed
            # "Extreme Greed" is typically FGI > 75. Let's use 70 for broader capture.
            is_extreme_greed = fear_greed_index >= 70

            # Condition 2: Momentum loss (fast EMA crossing below slow EMA)
            # We need to detect a *transition* for the cross, not just current state
            # Check if fast_ema was above slow_ema in the previous bar, and now it's below
            previous_closes = ctx.closes(self.slow_ema_period + self.rsi_period + 1)[-self.slow_ema_period - self.rsi_period - 2:-1]
            if len(previous_closes) < self.slow_ema_period + self.rsi_period + 1:
                return None # Not enough history for previous EMA

            prev_fast_ema = self._ema(previous_closes, self.fast_ema_period)
            prev_slow_ema = self._ema(previous_closes, self.slow_ema_period)

            ema_bearish_cross = (prev_fast_ema is not None and prev_slow_ema is not None and
                                 prev_fast_ema > prev_slow_ema and fast_ema < slow_ema)

            # Condition 3: RSI overbought (confirming overextension)
            # RSI typically overbought above 70. Let's use 65 for earlier entry.
            is_rsi_overbought = rsi >= 65

            if is_extreme_greed and ema_bearish_cross and is_rsi_overbought:
                self.last_signal_bar = ctx.bar_index
                confidence = min(1.0, (fear_greed_index - 70) / 20.0 + (rsi - 65) / 10.0) # Scale confidence
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "extreme_greed_ema_bearish_cross_rsi_overbought",
                        "fear_greed_index": fear_greed_index,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "current_price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters.
        # For this strategy, we mainly rely on the cooldown in on_bar.
        pass