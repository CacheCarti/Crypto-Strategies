from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndMonthStartETH(Strategy):
    METADATA = {
        "name": "Month End/Start ETH Strategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours * 3600 = 7200, allowing for month-end/start volatility
        "warmup_bars": 50,
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"]
        self.trade_cooldown_bars = 48 # Cooldown of 2 days (48 hours)
        self.month_end_lookback = 24 # Look back 24 hours for daily direction
        self.month_start_lookforward = 24 # Look forward 24 hours for daily direction
        self.daily_direction_threshold = 0.015 # 1.5% move for decisive daily direction
        self.volatility_regime_allow = ["NORMAL", "HIGH_VOL"] # Only trade in these regimes
        self.trend_regime_allow = ["neutral", "bull", "bear"] # Can trade in any trend regime
        self.last_month_end_bar = -1000 # To prevent re-triggering on the same month end
        self.last_month_start_bar = -1000 # To prevent re-triggering on the same month start


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if len(ctx.closes(self.METADATA["warmup_bars"])) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.trade_cooldown_bars:
            return None

        # Regime filters
        if ctx.market.get("regime", "NORMAL") not in self.volatility_regime_allow:
            return None

        if ctx.market.get("trend_regime", "neutral") not in self.trend_regime_allow:
            return None

        current_month = ctx.bar.timestamp.month
        next_month_start_timestamp = (ctx.bar.timestamp.replace(day=1) +
                                      (ctx.bar.timestamp.min - ctx.bar.timestamp.min).replace(month=1)).replace(month=current_month % 12 + 1)

        # Check for month-end transition (last day of the month)
        is_month_end = (ctx.bar.timestamp.month != next_month_start_timestamp.month) and \
                       (ctx.bar.timestamp.day == (next_month_start_timestamp - ctx.bar.timestamp.resolution).day)

        # Check for month-start transition (first day of the month)
        is_month_start = (ctx.bar.timestamp.day == 1)

        # Ensure we don't re-trigger on the same month event within a short period
        if is_month_end and ctx.bar_index - self.last_month_end_bar < self.trade_cooldown_bars:
            return None
        if is_month_start and ctx.bar_index - self.last_month_start_bar < self.trade_cooldown_bars:
            return None

        # Daily direction check
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # Long Entry: Month-end selling followed by month-start buying expectation
        if is_month_end and eth_returns < -self.daily_direction_threshold:
            # Check if current bar is the last bar of the day or close to it
            # This is a heuristic for daily closing behavior on 1h bars
            if ctx.bar.timestamp.hour >= 20: # Example: last few hours of the day
                self.last_trade_bar = ctx.bar_index
                self.last_month_end_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.65,
                    metadata={
                        "reason": "month_end_oversold_long",
                        "eth_return_pct": eth_returns,
                        "regime": ctx.market.get("regime"),
                        "trend_regime": ctx.market.get("trend_regime"),
                    }
                )

        # Short Entry: Month-end buying followed by month-start selling expectation
        if is_month_start and eth_returns > self.daily_direction_threshold:
            # Check if current bar is the first bar of the day or close to it
            if ctx.bar.timestamp.hour <= 4: # Example: first few hours of the day
                self.last_trade_bar = ctx.bar_index
                self.last_month_start_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.65,
                    metadata={
                        "reason": "month_start_overbought_short",
                        "eth_return_pct": eth_returns,
                        "regime": ctx.market.get("regime"),
                        "trend_regime": ctx.market.get("trend_regime"),
                    }
                )

        if ctx.has_position():
            # If position is held too long, consider exiting
            if ctx.bar_index - self.last_trade_bar > self.trade_cooldown_bars: # If we've passed the intended hold time
                return ctx.signal("flat", confidence=0.3, metadata={"reason": "holding_too_long_exit", "bars_held": ctx.bar_index - self.last_trade_bar})
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive sizing here based on position["return_bps"]
        # For this strategy, we keep it simple.