from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryTrendGate(Strategy):
    METADATA = {
        "name": "SentimentRecoveryTrendGate",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.fgi_period = 7 # Period to check FGI trend
        self.fgi_extreme_fear_threshold = 20
        self.fgi_recovery_threshold = 25 # FGI must be above this for recovery
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
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

        closes = ctx.closes(self.slow_ema_period + 1)
        highs = ctx.highs(self.rsi_period + 1)
        lows = ctx.lows(self.rsi_period + 1)

        if len(closes) < self.slow_ema_period + 1 or len(highs) < self.rsi_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        current_fgi = ctx.features.get("fear_greed_index", 50)
        
        # Determine FGI trend for recovery signal
        # We need historical FGI values to detect a recovery
        # For simplicity, we'll check if current FGI is rising from a low point
        # This is a proxy for historical FGI trend, as we don't have FGI history directly
        # A more robust solution would require storing FGI history in initialize/on_bar
        # Given the constraints, we check if current FGI is low but not extreme, and
        # that it's above previous bar's implied FGI (if it was extreme fear)
        
        # Simple FGI recovery check: Was it extremely fearful, and now it's improving but still low?
        is_fgi_recovering = False
        if current_fgi > self.fgi_extreme_fear_threshold and current_fgi < self.fgi_recovery_threshold:
            # This is a weak proxy for "recovery" without FGI history.
            # In a real scenario, we'd compare current_fgi to fgi_history[-1] or fgi_history[-self.fgi_period]
            # For this context, we assume if it's in the "recovering but still fearful" zone, it's a signal.
            # To make it more robust, we could check if the previous bar's FGI was below extreme_fear_threshold
            # (which would require storing FGI in self.fgi_history in initialize/on_bar)
            pass

        # To make "recovery" more explicit, let's assume we store the last FGI value.
        # This state needs to be managed carefully.
        # For this example, we simplify: if FGI is low (but not lowest possible) and has recently been lower.
        # If we had FGI history, this would be: fgi_history[-1] < self.fgi_extreme_fear_threshold and current_fgi > fgi_history[-1]
        # Since we don't have FGI history directly in `ctx`, we model it as:
        # If current FGI is moderately fearful, and was "extremely fearful" recently (heuristic)
        
        # Simulate FGI history for detection of recovery
        if not hasattr(self, 'fgi_history'):
            self.fgi_history = []
        self.fgi_history.append(current_fgi)
        if len(self.fgi_history) > self.fgi_period:
            self.fgi_history.pop(0)

        # Check for FGI recovery: FGI was in extreme fear, and now it's higher but still in fear zone
        fgi_was_extreme_fear = False
        if len(self.fgi_history) >= 2:
            # Check if the FGI was in extreme fear in the recent past (e.g., previous bar)
            if self.fgi_history[-2] <= self.fgi_extreme_fear_threshold:
                fgi_was_extreme_fear = True
        
        # A more robust recovery check: current FGI is improving from a very low point
        is_fgi_recovering = (
            fgi_was_extreme_fear and
            current_fgi > self.fgi_extreme_fear_threshold and
            current_fgi > self.fgi_history[-2] # Current FGI is higher than previous
        )

        # Market regime filter
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Long Entry Conditions
        if not ctx.has_position():
            if (fast_ema > slow_ema and # Bullish EMA crossover or trend
                rsi < 40 and # RSI indicating oversold in an uptrend
                is_fgi_recovering and # Sentiment recovering from extreme fear
                trend_regime == "bull" # Market trend is bullish
            ):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_recovery_ema_cross_rsi_oversold",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "fgi": current_fgi,
                        "trend_regime": trend_regime,
                    }
                )

        # Exit Conditions (no explicit exit logic beyond SL/TP for this strategy, but can add)
        # If we wanted to exit based on sentiment re-deterioration or trend change:
        if ctx.has_position() and ctx.position_direction() == "long":
            # Example: Exit if FGI drops back into extreme fear, or trend turns bearish
            if current_fgi <= self.fgi_extreme_fear_threshold or trend_regime == "bear":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "sentiment_deterioration_or_trend_change_exit",
                        "fgi": current_fgi,
                        "trend_regime": trend_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive logic
        # self.last_trade_bar is already updated in on_bar for explicit exits
        # For SL/TP exits, this ensures cooldown is respected from close time
        self.last_trade_bar = ctx.bar_index
        pass