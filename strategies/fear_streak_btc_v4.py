from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStreakRecovery(Strategy):
    METADATA = {
        "name": "Fear Greed Streak Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 12 * 3600,  # Aim for 12 hours
        "warmup_bars": 72,  # Need enough bars for 3 days of F&G, plus EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_greed_history = []
        self.last_fg_value = None
        self.in_fear_streak = False
        self.streak_start_bar = -1
        self.cooldown_bars = 0
        self.ema_period = 20 # for price-based confirmation
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Initialize to allow trading after warmup

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < 24: # Wait 24 bars (1 day) after a trade
            return None

        current_fg_index = ctx.features.get("fear_greed_index", 50)
        current_close = ctx.bar.close

        # Update fear_greed_history only on new daily F&G value
        if current_fg_index != self.last_fg_value:
            self.fear_greed_history.append(current_fg_index)
            if len(self.fear_greed_history) > 7: # Keep last 7 days for streak detection
                self.fear_greed_history.pop(0)
            self.last_fg_value = current_fg_index

        # Detect fear streak
        if len(self.fear_greed_history) >= 3 and all(fg < 25 for fg in self.fear_greed_history[-3:]):
            if not self.in_fear_streak:
                self.in_fear_streak = True
                self.streak_start_bar = ctx.bar_index
        elif self.in_fear_streak and current_fg_index >= 25: # Streak broken
            self.in_fear_streak = False

        # --- Entry Logic ---
        if not ctx.has_position():
            # Condition 1: In a fear streak and saw the first up day (current F&G > previous F&G)
            if self.in_fear_streak and len(self.fear_greed_history) >= 2 and \
               current_fg_index > self.fear_greed_history[-2]:

                # Condition 2: Price confirmation - current price above short-term EMA
                ema = self._ema(closes, self.ema_period)
                if ema is None:
                    return None

                if current_close > ema:
                    # Condition 3: Regime filter - avoid trading in "crisis" or strong "bear" trend
                    regime = ctx.market.get("regime", "NORMAL")
                    trend_regime = ctx.market.get("trend_regime", "neutral")
                    
                    if regime not in ["CRISIS", "MELTDOWN"] and trend_regime != "bear":
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75, # High confidence for this rare setup
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "fear_greed_streak_recovery_long",
                                "fear_greed_index": current_fg_index,
                                "previous_fear_greed_index": self.fear_greed_history[-2],
                                "current_close": current_close,
                                "ema": ema,
                                "regime": regime,
                                "trend_regime": trend_regime,
                            }
                        )
        # --- Exit Logic ---
        else: # If we have an open position
            if ctx.position_direction() == "long":
                # Exit Condition 1: Fear & Greed index recovers above 45
                if current_fg_index > 45:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "fear_greed_index_recovered",
                            "fear_greed_index": current_fg_index,
                            "entry_price": ctx.position["entry_price"],
                            "current_close": current_close,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset fear streak state if position was closed, to prevent immediate re-entry
        # This is a precaution, the cooldown should also prevent it.
        self.in_fear_streak = False
        self.streak_start_bar = -1
        # No need to update last_trade_bar here, it's updated on signal generation.
        pass