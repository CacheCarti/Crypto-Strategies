from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStreakFade(Strategy):
    METADATA = {
        "name": "VolatilityStreakFade_SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": [], # No specific features required for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5
        self.cooldown_bars = 10
        self.last_exit_bar = -self.cooldown_bars * 2 # Initialize far in the past

    def _is_up_bar(self, open_price, close_price):
        return close_price > open_price

    def _is_down_bar(self, open_price, close_price):
        return close_price < open_price

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.streak_period + 2)
        opens = ctx.opens(self.streak_period + 2)
        highs = ctx.highs(self.streak_period + 2)
        lows = ctx.lows(self.streak_period + 2)

        if len(closes) < self.streak_period + 2:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: Only trade in 'normal' or 'volatile' regimes, avoid 'crisis'
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Trend filter: Only fade bearish streaks in a neutral/bullish trend, and bullish streaks in a neutral/bearish trend
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Check for a bullish streak (N consecutive up bars)
        is_bullish_streak = True
        for i in range(self.streak_period):
            if not self._is_up_bar(opens[-(self.streak_period + 1 - i)], closes[-(self.streak_period + 1 - i)]):
                is_bullish_streak = False
                break

        # Check for a bearish streak (N consecutive down bars)
        is_bearish_streak = True
        for i in range(self.streak_period):
            if not self._is_down_bar(opens[-(self.streak_period + 1 - i)], closes[-(self.streak_period + 1 - i)]):
                is_bearish_streak = False
                break

        current_bar_is_up = self._is_up_bar(ctx.bar.open, ctx.bar.close)
        current_bar_is_down = self._is_down_bar(ctx.bar.open, ctx.bar.close)

        # Signal for fading a bullish streak failure
        if is_bullish_streak and current_bar_is_down: # Streak of up bars, followed by a down bar (failed continuation)
            if not ctx.has_position():
                # Only fade if trend is not strongly bullish, or neutral
                if trend_regime in ["bear", "neutral"] or (trend_regime == "bull" and trend_confidence < 0.6):
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_streak_fade",
                            "streak_length": self.streak_period,
                            "current_close": ctx.bar.close,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )

        # Signal for fading a bearish streak failure
        if is_bearish_streak and current_bar_is_up: # Streak of down bars, followed by an up bar (failed continuation)
            if not ctx.has_position():
                # Only fade if trend is not strongly bearish, or neutral
                if trend_regime in ["bull", "neutral"] or (trend_regime == "bear" and trend_confidence < 0.6):
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_streak_fade",
                            "streak_length": self.streak_period,
                            "current_close": ctx.bar.close,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index