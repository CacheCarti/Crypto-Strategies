from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMonthTurnStrategy(Strategy):
    METADATA = {
        "name": "SolMonthTurnStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -99999
        self.cooldown_bars = 48  # Cooldown for 2 days after a trade (48 1h bars)
        self.month_turn_window_hours = 12 # 12 hours before/after month turn
        self.min_daily_move_pct = 1.0 # 1% decisive daily move
        self.ema_period = 20 # For trend filtering

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_timestamp = ctx.bar.timestamp
        current_hour = current_timestamp.hour
        current_day = current_timestamp.day
        current_month = current_timestamp.month
        current_year = current_timestamp.year

        # Calculate previous day's close for daily move
        # We need enough bars to cover at least 24 hours plus lookback for EMA
        if len(closes) < 24 + self.ema_period:
            return None

        # Trend filter: Only trade if price is above/below EMA
        ema = self._ema(closes[:-1], self.ema_period) # EMA of previous bars
        if ema is None:
            return None
        
        is_uptrend = ctx.bar.close > ema
        is_downtrend = ctx.bar.close < ema

        # Volatility filter: Avoid trading in extreme volatility (crisis regime)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Check for month end / month start transition
        is_month_start_window = (current_day == 1 and current_hour < self.month_turn_window_hours)
        is_month_end_window = (
            current_day >= 28 and current_day < 31 and current_hour >= (24 - self.month_turn_window_hours) and current_month in [1,3,5,7,8,10,12] # 31 day months
        ) or (
            current_day >= 27 and current_day < 30 and current_hour >= (24 - self.month_turn_window_hours) and current_month in [4,6,9,11] # 30 day months
        ) or (
            current_day >= 26 and current_day < 29 and current_hour >= (24 - self.month_turn_window_hours) and current_month == 2 # Feb
        )
        
        if not (is_month_start_window or is_month_end_window):
            return None

        # Calculate daily returns for decisiveness
        # To get the previous day's close, we need to go back 24 bars.
        # This assumes consistent 1-hour bar data.
        if len(closes) < 24 * 2: # Need at least 2 full days
            return None

        prev_day_close = closes[-24]
        two_days_ago_close = closes[-48]
        
        daily_return_yesterday = ((ctx.bar.close - prev_day_close) / prev_day_close) * 100 if prev_day_close != 0 else 0
        daily_return_day_before = ((prev_day_close - two_days_ago_close) / two_days_ago_close) * 100 if two_days_ago_close != 0 else 0

        # Long entry conditions
        if not ctx.has_position() and is_uptrend:
            if is_month_start_window and daily_return_yesterday > self.min_daily_move_pct and daily_return_day_before > self.min_daily_move_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "month_start_bullish_momentum_uptrend",
                        "daily_return_yesterday": daily_return_yesterday,
                        "daily_return_day_before": daily_return_day_before,
                        "ema": ema,
                        "price": ctx.bar.close,
                        "timestamp": current_timestamp.isoformat()
                    }
                )
            elif is_month_end_window and daily_return_yesterday < -self.min_daily_move_pct and daily_return_day_before < -self.min_daily_move_pct:
                # Reversion play: strong down move into month end, expect bounce
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.6,
                    metadata={
                        "reason": "month_end_bearish_reversion_uptrend",
                        "daily_return_yesterday": daily_return_yesterday,
                        "daily_return_day_before": daily_return_day_before,
                        "ema": ema,
                        "price": ctx.bar.close,
                        "timestamp": current_timestamp.isoformat()
                    }
                )

        # Short entry conditions
        if not ctx.has_position() and is_downtrend:
            if is_month_start_window and daily_return_yesterday < -self.min_daily_move_pct and daily_return_day_before < -self.min_daily_move_pct:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "month_start_bearish_momentum_downtrend",
                        "daily_return_yesterday": daily_return_yesterday,
                        "daily_return_day_before": daily_return_day_before,
                        "ema": ema,
                        "price": ctx.bar.close,
                        "timestamp": current_timestamp.isoformat()
                    }
                )
            elif is_month_end_window and daily_return_yesterday > self.min_daily_move_pct and daily_return_day_before > self.min_daily_move_pct:
                # Reversion play: strong up move into month end, expect pullback
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.6,
                    metadata={
                        "reason": "month_end_bullish_reversion_downtrend",
                        "daily_return_yesterday": daily_return_yesterday,
                        "daily_return_day_before": daily_return_day_before,
                        "ema": ema,
                        "price": ctx.bar.close,
                        "timestamp": current_timestamp.isoformat()
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed here for this strategy,
        # but could be used for logging or dynamic cooldown adjustments.
        pass