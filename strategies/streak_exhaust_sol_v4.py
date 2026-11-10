from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionSol(Strategy):
    METADATA = {
        "name": "Streak Exhaustion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.streak_length = 5
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars
        self.min_confidence = 0.6
        self.max_funding_rate = 0.0005 # 0.05% per 8h, indicating overheated longs
        self.min_fear_greed_for_long = 30 # Only long if not extreme greed
        self.max_fear_greed_for_short = 70 # Only short if not extreme fear

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
        closes = ctx.closes(self.slow_ema_period + 1)
        opens = ctx.opens(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Calculate streak of consecutive directional bars
        long_streak = 0
        short_streak = 0
        for i in range(1, len(closes)):
            if closes[i] > opens[i]: # Green bar
                long_streak += 1
                short_streak = 0
            elif closes[i] < opens[i]: # Red bar
                short_streak += 1
                long_streak = 0
            else: # Doji
                long_streak = 0
                short_streak = 0

        # Market context and feature filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Long signal conditions (fade short streak)
        if (
            not ctx.has_position()
            and short_streak >= self.streak_length
            and current_close > current_open # Current bar is green (failed continuation of short streak)
            and current_close > fast_ema # Price above fast EMA
            and fast_ema > slow_ema # Bullish EMA crossover or trend
            and regime in ["NORMAL", "HIGH_VOL"] # Avoid crisis
            and trend_regime != "bear" # Avoid strong bearish trend
            and fear_greed_index < self.max_fear_greed_for_short # Not extreme greed
            and funding_rate < self.max_funding_rate # Not excessively positive funding
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=self.min_confidence,
                metadata={
                    "reason": "short_streak_exhaustion_fade",
                    "short_streak": short_streak,
                    "current_close": current_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed_index,
                    "funding_rate": funding_rate,
                },
            )

        # Short signal conditions (fade long streak)
        if (
            not ctx.has_position()
            and long_streak >= self.streak_length
            and current_close < current_open # Current bar is red (failed continuation of long streak)
            and current_close < fast_ema # Price below fast EMA
            and fast_ema < slow_ema # Bearish EMA crossover or trend
            and regime in ["NORMAL", "HIGH_VOL"] # Avoid crisis
            and trend_regime != "bull" # Avoid strong bullish trend
            and fear_greed_index > self.min_fear_greed_for_long # Not extreme fear
            and funding_rate > -self.max_funding_rate # Not excessively negative funding
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=self.min_confidence,
                metadata={
                    "reason": "long_streak_exhaustion_fade",
                    "long_streak": long_streak,
                    "current_close": current_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "regime": regime,
                    "trend_regime": trend_regime,
                    "fear_greed_index": fear_greed_index,
                    "funding_rate": funding_rate,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt sizing.
        pass