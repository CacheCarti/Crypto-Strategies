from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutReversion(Strategy):
    METADATA = {
        "name": "Failed Breakout Reversion (BTC)",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Aim for a few hours
        "warmup_bars": 50,  # Need enough bars for range and EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20  # Bars to define the range
        self.ema_period = 20   # EMA for trend filter
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.cooldown_bars + 1)
        highs = ctx.highs(self.range_period + self.cooldown_bars + 1)
        lows = ctx.lows(self.range_period + self.cooldown_bars + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Calculate range using the most recent `range_period` bars
        range_high = max(highs[-self.range_period-1:-1]) # Exclude current bar for range definition
        range_low = min(lows[-self.range_period-1:-1])   # Exclude current bar for range definition

        # Calculate EMA for trend filter
        ema_val = self._ema(closes[:-1], self.ema_period) # EMA from previous closes
        if ema_val is None:
            return None

        # Fear & Greed Index as a sentiment filter
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Check for failed breakouts only if not in a position
        if not ctx.has_position():
            # Long setup: Price broke below range_low, then closed back above it
            # And current price is above EMA (bullish bias)
            # And not in extreme greed (avoid topping out)
            if (closes[-2] < range_low and current_close > range_low and
                current_close > ema_val and fear_greed < 75):
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "failed_breakdown_reversion",
                        "entry_price": current_close,
                        "range_low": range_low,
                        "previous_close": closes[-2],
                        "ema_val": ema_val,
                        "fear_greed": fear_greed
                    }
                )

            # Short setup: Price broke above range_high, then closed back below it
            # And current price is below EMA (bearish bias)
            # And not in extreme fear (avoid bottoming out)
            elif (closes[-2] > range_high and current_close < range_high and
                  current_close < ema_val and fear_greed > 25):
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "failed_breakout_reversion",
                        "entry_price": current_close,
                        "range_high": range_high,
                        "previous_close": closes[-2],
                        "ema_val": ema_val,
                        "fear_greed": fear_greed
                    }
                )
        
        # If in a position, check for exit conditions based on range midpoint or opposite edge
        elif ctx.has_position():
            mid_point = (range_high + range_low) / 2

            if ctx.position_direction() == "long":
                # Exit long if price reaches range_high or midpoint for partial profit
                if current_close >= range_high or current_close >= mid_point:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "long_target_hit",
                            "exit_price": current_close,
                            "range_high": range_high,
                            "mid_point": mid_point
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if price reaches range_low or midpoint for partial profit
                if current_close <= range_low or current_close <= mid_point:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "short_target_hit",
                            "exit_price": current_close,
                            "range_low": range_low,
                            "mid_point": mid_point
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters
        pass