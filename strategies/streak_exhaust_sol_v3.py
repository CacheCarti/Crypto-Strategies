from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionSOL(Strategy):
    METADATA = {
        "name": "Streak Exhaustion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for 1-2 bars hold
        "warmup_bars": 50, # Needs enough bars for EMA and streak calculation
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5  # Number of consecutive bars to define a streak
        self.ema_period = 20    # EMA for trend filtering
        self.rsi_period = 14    # RSI for overbought/oversold confirmation
        self.cooldown_bars = 5  # Bars to wait after a trade closes
        self.last_trade_bar_index = -self.cooldown_bars

        # State to track streaks
        self.consecutive_bull_bars = 0
        self.consecutive_bear_bars = 0
        self.last_close = None

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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, neutral if no change
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one more for RSI diff
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_close = closes[-1]
        previous_close = closes[-2] if len(closes) > 1 else None

        # Update streak counter
        if self.last_close is not None:
            if current_close > self.last_close:
                self.consecutive_bull_bars += 1
                self.consecutive_bear_bars = 0
            elif current_close < self.last_close:
                self.consecutive_bear_bars += 1
                self.consecutive_bull_bars = 0
            else: # neutral bar
                self.consecutive_bull_bars = 0
                self.consecutive_bear_bars = 0
        self.last_close = current_close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Calculate indicators
        current_ema = self._ema(closes, self.ema_period)
        current_rsi = self._rsi(closes, self.rsi_period)

        if current_ema is None or current_rsi is None:
            return None

        # Market context filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long exhaustion setup:
        # Extreme bearish streak, then a failed continuation (current bar closes above previous close)
        # And price is below EMA, RSI oversold
        if (self.consecutive_bear_bars >= self.streak_period and
            current_close > previous_close and # Failed continuation (bounce)
            current_close < current_ema and # Price below EMA (downtrend)
            current_rsi < 30 and # RSI oversold
            not ctx.has_position()):

            # Add a filter for funding rate (negative funding implies short pressure, good for long fade)
            # And fear/greed (extreme fear often marks bottoms)
            if funding_rate < -0.00005 and fear_greed_index < 30: # Example thresholds
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bear_streak_exhaustion_fade",
                        "consecutive_bear_bars": self.consecutive_bear_bars,
                        "current_close": current_close,
                        "ema": current_ema,
                        "rsi": current_rsi,
                        "funding_rate": funding_rate,
                        "fear_greed_index": fear_greed_index
                    }
                )

        # Short exhaustion setup:
        # Extreme bullish streak, then a failed continuation (current bar closes below previous close)
        # And price is above EMA, RSI overbought
        elif (self.consecutive_bull_bars >= self.streak_period and
              current_close < previous_close and # Failed continuation (rejection)
              current_close > current_ema and # Price above EMA (uptrend)
              current_rsi > 70 and # RSI overbought
              not ctx.has_position()):

            # Add a filter for funding rate (positive funding implies long pressure, good for short fade)
            # And fear/greed (extreme greed often marks tops)
            if funding_rate > 0.00005 and fear_greed_index > 70: # Example thresholds
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bull_streak_exhaustion_fade",
                        "consecutive_bull_bars": self.consecutive_bull_bars,
                        "current_close": current_close,
                        "ema": current_ema,
                        "rsi": current_rsi,
                        "funding_rate": funding_rate,
                        "fear_greed_index": fear_greed_index
                    }
                )

        # If we have a position, hold it.
        if ctx.has_position():
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset streak counters after a position closes if desired, or use for adaptive sizing
        # For this strategy, we keep streak counters continuous.
        # However, we mark the last trade time for cooldown.
        pass