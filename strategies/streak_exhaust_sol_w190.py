from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ExhaustionFade(Strategy):
    METADATA = {
        "name": "Exhaustion Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_period = 5  # Number of consecutive directional bars to define a streak
        self.fade_period = 3    # Lookback for failed continuation
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _get_consecutive_direction(self, closes, opens):
        if len(closes) < self.streak_period:
            return 0

        up_streak = 0
        down_streak = 0

        # Check for bullish streak
        is_up_streak = True
        for i in range(1, self.streak_period + 1):
            if closes[-i] <= opens[-i]: # closed flat or down
                is_up_streak = False
                break
        if is_up_streak:
            up_streak = self.streak_period

        # Check for bearish streak
        is_down_streak = True
        for i in range(1, self.streak_period + 1):
            if closes[-i] >= opens[-i]: # closed flat or up
                is_down_streak = False
                break
        if is_down_streak:
            down_streak = self.streak_period

        return up_streak if up_streak > 0 else -down_streak

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.streak_period + self.fade_period + 1)
        opens = ctx.opens(self.streak_period + self.fade_period + 1)
        highs = ctx.highs(self.streak_period + self.fade_period + 1)
        lows = ctx.lows(self.streak_period + self.fade_period + 1)
        volumes = ctx.volumes(self.streak_period + self.fade_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Calculate a simple EMA for trend filtering and mean-reversion target
        ema_period = 20
        if len(closes) < ema_period:
            return None
        
        ema_values = []
        k = 2.0 / (ema_period + 1)
        # Calculate EMA from past data up to current bar's open
        if len(closes) > ema_period:
            ema = sum(closes[-(ema_period+1):-1]) / ema_period # EMA up to previous close
            for i in range(ema_period, len(closes)-1):
                ema = closes[i] * k + ema * (1 - k)
            current_ema = closes[-1] * k + ema * (1-k) # This is the current bar's EMA
        else:
            current_ema = sum(closes) / len(closes) # simple average if not enough data for full EMA

        # Check for consecutive directional bars (streak)
        # We need data from (streak_period + fade_period) bars ago to check the streak ending at fade_period ago
        streak = self._get_consecutive_direction(closes[:-self.fade_period], opens[:-self.fade_period])

        # Get Fear & Greed Index for sentiment filtering
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        if not ctx.has_position():
            # Long setup: Bearish streak followed by failed downward continuation
            if streak < 0 and closes[-1] > current_open: # Streak was bearish, current bar is green (failed continuation)
                # Ensure current bar closed above its open and is relatively strong
                if current_close > current_open and (current_close - current_open) / current_open > 0.002: # 0.2% up
                    # Add a filter: only trade if price is below EMA or relatively close to it (mean reversion)
                    if current_close < current_ema * 1.01 and fear_greed_index < 50: # Slightly below EMA and some fear
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_streak_fade_long",
                                "streak_length": abs(streak),
                                "current_close": current_close,
                                "current_open": current_open,
                                "ema20": current_ema,
                                "fear_greed": fear_greed_index,
                            }
                        )

            # Short setup: Bullish streak followed by failed upward continuation
            elif streak > 0 and closes[-1] < current_open: # Streak was bullish, current bar is red (failed continuation)
                # Ensure current bar closed below its open and is relatively strong
                if current_close < current_open and (current_open - current_close) / current_open > 0.002: # 0.2% down
                    # Add a filter: only trade if price is above EMA or relatively close to it (mean reversion)
                    if current_close > current_ema * 0.99 and fear_greed_index > 50: # Slightly above EMA and some greed
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_streak_fade_short",
                                "streak_length": abs(streak),
                                "current_close": current_close,
                                "current_open": current_open,
                                "ema20": current_ema,
                                "fear_greed": fear_greed_index,
                            }
                        )
        
        # If we have a position, we just hold and let the engine manage SL/TP
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can use this to adjust confidence or cooldowns, but for now,
        # the self.last_trade_bar update handles cooldown.
        pass