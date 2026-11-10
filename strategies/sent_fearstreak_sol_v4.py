from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakSOL(Strategy):
    METADATA = {
        "name": "Sentiment Fear Streak SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 70, # Need enough bars for 50-period indicators
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_period = 5 # How many daily readings to check
        self.min_fear_greed_threshold = 30 # Max F&G for a "fear" reading
        self.ema_period_slow = 50
        self.ema_period_fast = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.min_volume_ratio = 1.2 # Volume confirmation
        self.min_regime_confidence = 0.6 # Confidence for trend regime
        self.min_crisis_score = 0.3 # Max crisis score to trade

        # Store daily F&G values to check for streak
        self.daily_fg_history = []
        self.last_fg_day = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = values[0] # Initialize with first value, or SMA
        if len(values) > period:
             ema_val = self._sma(values[:period], period) # Use SMA for initial EMA for accuracy
             if ema_val is None: return None
        else:
             ema_val = sum(values) / len(values) # Simple average for short list

        for i in range(1, len(values)):
            ema_val = values[i] * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period_slow + 1)
        volumes = ctx.volumes(self.ema_period_slow)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_day = ctx.bar.timestamp.day
        if self.last_fg_day != current_day:
            # New day, update daily F&G history
            current_fg = ctx.features.get("fear_greed_index", 50)
            self.daily_fg_history.append(current_fg)
            # Keep history to the required period
            if len(self.daily_fg_history) > self.fear_greed_period:
                self.daily_fg_history.pop(0)
            self.last_fg_day = current_day

        # Check for fear streak
        fear_streak = False
        if len(self.daily_fg_history) >= self.fear_greed_period:
            if all(fg <= self.min_fear_greed_threshold for fg in self.daily_fg_history[-self.fear_greed_period:]):
                fear_streak = True

        # Calculate EMAs
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Volume confirmation (current volume vs. average volume)
        avg_volume = self._sma(volumes, self.ema_period_fast) # Use fast EMA period for volume avg
        volume_ratio = ctx.bar.volume / avg_volume if avg_volume else 1.0

        # Regime filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Entry logic: Long only if fear streak, price stabilizes (EMA cross), volume confirms, and regimes are favorable
        if not ctx.has_position():
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None # Cooldown active

            # Condition 1: Fear streak
            if fear_streak:
                # Condition 2: Price stabilization (fast EMA crosses above slow EMA)
                # Check for an actual cross, not just being above
                if ema_fast > ema_slow and closes[-2] < ema_slow: # Previous bar fast EMA was below or equal slow EMA
                    # Condition 3: Volume confirmation
                    if volume_ratio >= self.min_volume_ratio:
                        # Condition 4: Favorable market regime
                        if trend_regime == "bear" and trend_confidence >= self.min_regime_confidence: # Look for bounce in bear trend
                            if crisis_score < self.min_crisis_score: # Not in a full-blown crisis
                                return ctx.signal(
                                    "long",
                                    confidence=0.75,
                                    metadata={
                                        "reason": "fear_streak_ema_cross_volume_confirm_bear_regime",
                                        "fear_greed_avg": sum(self.daily_fg_history) / len(self.daily_fg_history),
                                        "ema_fast": ema_fast,
                                        "ema_slow": ema_slow,
                                        "volume_ratio": volume_ratio,
                                        "trend_regime": trend_regime,
                                        "trend_confidence": trend_confidence,
                                        "crisis_score": crisis_score,
                                        "current_price": ctx.bar.close,
                                    }
                                )
        # Exit logic: Flat if EMA fast crosses below EMA slow, or if fear streak ends
        else:
            if ctx.position_direction() == "long":
                # Exit condition 1: Fast EMA crosses below slow EMA
                if ema_fast < ema_slow and closes[-2] > ema_slow:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "ema_bear_cross_exit",
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": ctx.bar.close,
                        }
                    )
                # Exit condition 2: Fear streak ends (optional, might make it too sensitive)
                # if not fear_streak:
                #     return ctx.signal(
                #         "flat",
                #         confidence=0.6,
                #         metadata={
                #             "reason": "fear_streak_ended_exit",
                #             "fear_greed_avg": sum(self.daily_fg_history) / len(self.daily_fg_history),
                #             "current_price": ctx.bar.close,
                #         }
                #     )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # You can add adaptive logic here based on position performance
        # For example, adjust confidence or cooldown based on return_bps
        pass