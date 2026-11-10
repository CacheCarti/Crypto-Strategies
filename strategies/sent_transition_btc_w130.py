from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedReversalStrategy(Strategy):
    METADATA = {
        "name": "FearGreed Reversal BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6 # Cooldown for 6 bars (6 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.rsi_period = 14
        self.fear_greed_threshold = 25 # Extreme Fear is generally below 20-25
        self.fear_greed_recovery_delta = 5 # How much the FGI must increase to signal recovery

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_fear_greed_index = ctx.features.get("fear_greed_index", 50)
        previous_fear_greed_index = ctx.features.get("fear_greed_index", 50, offset=-1) # Get previous day's FGI

        # Check for Fear & Greed recovery from extreme fear
        is_recovering_from_fear = (
            previous_fear_greed_index <= self.fear_greed_threshold and
            current_fear_greed_index > previous_fear_greed_index + self.fear_greed_recovery_delta
        )

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Long entry conditions
        if not ctx.has_position():
            # Regime filter: FGI recovering from extreme fear
            if is_recovering_from_fear:
                # Price action filter: EMA fast crossing above EMA slow
                if ema_fast > ema_slow and closes[-1] > closes[-2]: # current price is above previous
                    # Momentum filter: RSI is not overbought, allowing for upward movement
                    if rsi < 70:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "fear_greed_recovery_ema_bull_cross",
                                "fear_greed_index": current_fear_greed_index,
                                "prev_fear_greed_index": previous_fear_greed_index,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "rsi": rsi,
                                "close": closes[-1]
                            }
                        )

        # No short logic for this strategy as it's a fear-recovery long play
        # If in position, hold.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing for a new trade after the cooldown period
        self.last_trade_bar = ctx.bar_index
        # You could also use this for adaptive sizing or logging
        # print(f"Position closed: {position['direction']} at {position['exit_price']} with return {position['return_bps']} bps")