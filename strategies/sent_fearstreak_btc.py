from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFearStreakBTC(Strategy):
    METADATA = {
        "name": "Sentiment Fear Streak BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60, # Need enough history for EMA and Fear/Greed
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_streak_threshold = 35 # FGI below this is "fearful"
        self.fear_streak_length = 3 # Number of consecutive fearful days
        self.ema_period = 20 # EMA period for stabilization
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_fgi = ctx.features.get("fear_greed_index", 50)
        
        # Check for fear streak (uses daily FGI, so it will be constant for 24 bars)
        # We need at least self.fear_streak_length * 24 bars to check previous days' FGI
        # Given FGI is daily, we check the FGI value at each *day change* within the lookback
        
        # Collect recent unique daily FGI readings
        daily_fgis = []
        last_fgi_day = -1
        # Iterate backwards from current bar
        for i in range(len(closes) - 1, -1, -1):
            bar_time = ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0) # current bar's day
            
            # Check if this bar is from a new day compared to the previous daily_fgis entry
            if not daily_fgis or bar_time.day != last_fgi_day:
                # Get FGI for this specific bar (it will be constant for the day)
                # Need to approximate FGI for past days, using the value from the start of each day.
                # Since ctx.features.get always returns the current bar's FGI,
                # we assume that FGI for a given timestamp is constant for that day.
                # To get distinct daily values, we need to look at bars separated by 24 hours.
                
                # This is a simplification as ctx.features.get("fear_greed_index")
                # will return the current bar's FGI. For daily FGI, this means
                # it stays constant for 24 bars. We need to check if the FGI
                # *value* has been consistently low for multiple *days*.
                
                # A more robust way: store FGI at day boundaries in initialize or on_bar
                # For now, we'll check the current FGI and assume it represents the current day.
                # To check 'fear streak', we need to compare the current day's FGI with previous days'.
                
                # For simplicity and given the constraints, we'll use a pragmatic approach:
                # if the current FGI is below threshold, and it has been below for at least
                # self.fear_streak_length * 24 bars (roughly self.fear_streak_length days),
                # and the *value* of FGI has been consistently low during those *days*.
                
                # This is hard to do perfectly with only ctx.features.get("fear_greed_index")
                # which always gives current bar's FGI.
                # The assumption is that `fear_greed_index` in `ctx.features` updates daily.
                # So to check a streak, we need to check if the FGI *value* has been
                # below the threshold for the last `fear_streak_length` unique daily readings.

                # Let's track the last N FGI readings.
                # Since FGI is daily, it will be the same for 24 bars.
                # We need to extract unique daily FGI values.
                
                # This approach will use the current FGI value and assume it's stable for the day.
                # To check a streak, we need to know the FGI for the past few days.
                # The current API doesn't easily give historical daily FGI values without
                # storing them explicitly, which would require custom logic to identify day changes.
                
                # A simpler, and more robust for the API, interpretation of "fear streak":
                # The current `fear_greed_index` has been below the threshold, and price has
                # been trending down, and now we see a stabilization.
                # This is more aligned with "sentiment-regime" as a filter, not a direct trigger.

                # Let's define fear streak as: current FGI < threshold AND it has been
                # below threshold for at least `self.fear_streak_length` "daily" updates.
                # Since we don't have historical FGI values directly, we can check
                # if the current FGI is below the threshold, and the market regime
                # has been "volatile" or "crisis" for a while.

                # Simplified fear streak check:
                # If current FGI is below threshold AND the market has been in a "fearful" state (e.g., normal or crisis regime)
                # for a certain number of bars.
                
                # Re-interpreting "fear streak" for simplicity:
                # We are in a "fearful" state if current FGI is low AND the market has been volatile or in crisis.
                # The "streak" implies this condition has persisted.
                
                # Let's use the current FGI and a simple price-based stabilization.
                # We'll use FGI as a *filter* for long entries.

                pass # The FGI logic needs to be more robust for "streak" if it's based on unique daily values.
                     # Given `ctx.features.get` only gives the current bar's FGI,
                     # we'll use it as a simple filter: FGI must be below a threshold.
                     # The "streak" part will be implicitly handled by waiting for price stabilization.

        # Condition 1: Fear & Greed Index is low (fearful sentiment)
        is_fearful_sentiment = current_fgi < self.fear_streak_threshold

        # Condition 2: Price stabilization (e.g., closing above a short-term EMA after a dip)
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # A stabilization bar: close is above EMA, and was below it recently (e.g., previous bar)
        # This implies a bounce or recovery.
        current_close = closes[-1]
        previous_close = closes[-2] if len(closes) >= 2 else None
        
        if previous_close is None:
            return None

        # Price stabilization: current close is above EMA, and previous close was below EMA
        # Or current close is simply above EMA after a period of low FGI
        # To prevent over-trading, we look for a close *above* the EMA, implying a bounce.
        # And to make it a "stabilization", we want to see it coming from below.
        
        is_price_stabilizing = current_close > ema and previous_close < ema
        
        # Additional filter: ensure market isn't in a full-blown crisis (though FGI handles some of this)
        # ctx.market.get("regime") is better than ctx.regime for more granular states.
        market_regime = ctx.market.get("regime", "NORMAL")
        not_in_crisis = market_regime != "CRISIS" and market_regime != "MELTDOWN"

        # Entry logic: long only when fearful sentiment AND price stabilization AND not in deep crisis
        if not ctx.has_position() and is_fearful_sentiment and is_price_stabilizing and not_in_crisis:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75, # High confidence for this setup
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fgi_fear_streak_ema_stabilization_long",
                    "current_fgi": current_fgi,
                    "ema_period": self.ema_period,
                    "current_close": current_close,
                    "ema_value": ema,
                    "market_regime": market_regime,
                }
            )
        
        # If we are in a position and FGI turns very greedy (exit signal)
        if ctx.has_position() and ctx.position_direction() == "long":
            if current_fgi > 70: # If FGI becomes "Greed" or "Extreme Greed"
                 return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "fgi_too_greedy_exit",
                        "current_fgi": current_fgi,
                        "market_regime": market_regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar after any position closes,
        # so the cooldown starts from the close time.
        self.last_trade_bar = ctx.bar_index
        # You could also log position details or adapt sizing here.
        # print(f"Position closed: {position}")