from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcMondayRebuild(Strategy):
    METADATA = {
        "name": "BTC Monday Rebuild",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 days average hold
        "warmup_bars": 72, # For 3-day lookback for weekend move
        "required_features": ["fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.weekend_lookback_period = 48 # 2 days of 1-hour bars
        self.monday_confirmation_period = 6 # 6 hours for US session confirmation
        self.cooldown_bars = 48 # Cooldown after a trade (2 days)
        self.last_trade_bar = -self.cooldown_bars

        # For SMA calculation
        self.sma_period = 24 # 1 day SMA
        self.price_change_threshold = 0.005 # 0.5% move over weekend

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Only consider trades on Monday
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            return None

        # Only consider trades during US market hours (e.g., 13:00 - 18:00 UTC)
        # This aligns with the "first US hours" confirmation
        if not (13 <= ctx.bar.timestamp.hour < 18):
            return None

        # Regime filter: Only trade in NORMAL or VOLATILE regimes, not CRISIS
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Trend filter: Only trade with a neutral or weak trend
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime not in ["neutral", "bull", "bear"]: # Allow bull/bear but with low confidence
            return None
        
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        if trend_confidence > 0.6 and trend_regime != "neutral": # If strong trend, avoid counter-trend
             return None

        # Calculate weekend move (Friday close to Sunday close)
        # Find Friday's last close (end of week - 2 days - 1 day for Sunday)
        # Assuming bar.timestamp is the start of the bar
        if len(closes) < self.weekend_lookback_period + self.monday_confirmation_period + 24: # Need enough history for Friday close
            return None

        # Approximate Friday close (last bar before weekend, 24 hours before Sunday's start)
        # This is tricky with exact timestamps. Let's simplify: look at the price change over the last 48 bars (Fri-Sun)
        # More robust: find the close from 72 hours ago (start of Friday) and compare to 24 hours ago (start of Sunday)
        # Or, simpler: compare close 48 hours ago (approx Friday close) to current close (Monday).
        # We need the weekend move BEFORE Monday's confirmation.
        # Let's use the price change from roughly 48 hours ago (end of Friday/start of Saturday) to 24 hours ago (end of Sunday/start of Monday)
        # This is still difficult to pinpoint without exact bar timestamps relative to day boundaries.

        # Simplified approach: Look at the last N bars (representing the weekend)
        # and assess the direction leading into Monday's open.
        # Let's say the 'weekend move' is the change from (current bar - 48 hours) to (current bar - 24 hours)
        # But we need the move *before* Monday's confirmation.
        # Let's define weekend move as the difference between the close 48 bars ago (approx Friday) and the close 24 bars ago (approx Sunday).
        
        # To get the weekend move (Friday close to Sunday close), we need:
        # 1. Close 48 hours ago (approx end of Friday/start of Saturday)
        # 2. Close 24 hours ago (approx end of Sunday/start of Monday)
        
        # Let's use `ctx.closes` to get values relative to the current bar.
        # `closes[-1]` is current bar close. `closes[-2]` is prior bar close.
        # `closes[-25]` is close 24 hours ago. `closes[-49]` is close 48 hours ago.

        friday_close_approx = closes[-49] # Close 48 bars ago, roughly Friday's end
        sunday_close_approx = closes[-25] # Close 24 bars ago, roughly Sunday's end

        weekend_change_pct = (sunday_close_approx - friday_close_approx) / friday_close_approx

        # Now, check Monday's confirmation (last 6 hours)
        monday_confirmation_closes = closes[-self.monday_confirmation_period:]
        if len(monday_confirmation_closes) < self.monday_confirmation_period:
            return None

        monday_confirmation_change_pct = (monday_confirmation_closes[-1] - monday_confirmation_closes[0]) / monday_confirmation_closes[0]

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long condition: Weekend down, Monday confirms up
        if weekend_change_pct < -self.price_change_threshold: # Significant drop over weekend
            if monday_confirmation_change_pct > (self.price_change_threshold / 2): # Monday rebuilds upwards
                signal_direction = "long"
                reason = "btc_monday_rebuild_long"
                confidence = 0.65 + ctx.rng.uniform(0, 0.1) # Add a touch of randomness for confidence

        # Short condition: Weekend up, Monday confirms down
        elif weekend_change_pct > self.price_change_threshold: # Significant rise over weekend
            if monday_confirmation_change_pct < -(self.price_change_threshold / 2): # Monday rebuilds downwards
                signal_direction = "short"
                reason = "btc_monday_rebuild_short"
                confidence = 0.65 + ctx.rng.uniform(0, 0.1)

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "weekend_change_pct": weekend_change_pct,
                    "monday_confirmation_change_pct": monday_confirmation_change_pct,
                    "bar_timestamp": str(ctx.bar.timestamp),
                    "regime": ctx.market.get("regime", "NORMAL"),
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence,
                    "fear_greed_index": ctx.features.get("fear_greed_index", 50)
                }
            )

        # If already in a position, hold
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown
        # This is already handled by setting self.last_trade_bar upon signal, but good to ensure
        # self.last_trade_bar = ctx.bar_index # This would reset cooldown on close, which we don't want
        pass # Cooldown is handled at entry signal generation