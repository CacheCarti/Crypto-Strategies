from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryMomentum(Strategy):
    METADATA = {
        "name": "Sentiment Recovery Momentum (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours hold time for 1h bars
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store previous fear_greed_index to detect recovery
        self.prev_fear_greed_index = 50

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[0:period]) / period # Simple average for initial EMA
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
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1) # Need one more for EMA prev
        if len(closes) < self.METADATA["warmup_bars"]:
            self.prev_fear_greed_index = ctx.features.get("fear_greed_index", 50)
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            self.prev_fear_greed_index = ctx.features.get("fear_greed_index", 50)
            return None

        current_fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Check for sentiment recovery from extreme fear
        # We want to enter when FGI was low (e.g., < 25) and is now rising
        # but not just *low*, but recovering from *extreme* low.
        is_recovering_from_fear = (self.prev_fear_greed_index < 25 and current_fear_greed_index > self.prev_fear_greed_index)

        signal_confidence = 0.0
        signal_direction = "hold"
        reason = "no_signal"
        trigger_values = {}

        if not ctx.has_position():
            # Long entry conditions
            if is_recovering_from_fear and \
               fast_ema > slow_ema and \
               rsi < self.rsi_oversold: # RSI is still oversold but momentum is picking up, FGI recovering
                
                signal_direction = "long"
                signal_confidence = 0.75
                reason = "fear_recovery_ema_cross_rsi_oversold"
                trigger_values = {
                    "fgi_prev": self.prev_fear_greed_index,
                    "fgi_current": current_fear_greed_index,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "rsi": rsi,
                    "price": current_price
                }
        elif ctx.position_direction() == "long":
            # Exit long conditions
            # Take profit or stop loss is handled by the engine's declared_sl/tp_bps
            # Additional exit: if fast EMA crosses below slow EMA or RSI becomes overbought
            if fast_ema < slow_ema and closes[-1] < fast_ema:
                signal_direction = "flat"
                signal_confidence = 0.6
                reason = "long_exit_ema_bearish_cross"
                trigger_values = {
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": current_price
                }
            elif rsi > self.rsi_overbought:
                signal_direction = "flat"
                signal_confidence = 0.5
                reason = "long_exit_rsi_overbought"
                trigger_values = {
                    "rsi": rsi,
                    "price": current_price
                }

        self.prev_fear_greed_index = current_fear_greed_index

        if signal_direction != "hold":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": reason, **trigger_values}
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could adapt sizing or cooldown here based on performance,
        # but for simplicity, we'll just log or acknowledge.
        # The cooldown is handled in on_bar directly.
        pass