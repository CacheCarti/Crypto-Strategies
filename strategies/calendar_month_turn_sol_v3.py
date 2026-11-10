from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMonthTurnStrategy(Strategy):
    METADATA = {
        "name": "SolMonthTurnStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 24, # Up to 24 hours for month-turn
        "warmup_bars": 72, # Sufficient for 3-day lookback at 1h bars
        "required_features": [], # No external features needed for this calendar strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.month_turn_window = 24 # hours before/after month start
        self.min_daily_move_pct = 0.02 # 2% daily move to consider decisive
        self.cooldown_bars = 48 # Cooldown for 2 days after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _get_daily_change(self, closes, period):
        if len(closes) < period:
            return 0.0
        return (closes[-1] - closes[-period]) / closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Check for month-end/month-start window
        is_month_end_window = (current_day == ctx.bar.timestamp.replace(day=1).day - 1) and (current_hour >= 24 - self.month_turn_window)
        is_month_start_window = (current_day == 1) and (current_hour < self.month_turn_window)

        if not (is_month_end_window or is_month_start_window):
            return None

        # Ensure enough data for daily move calculation
        if len(ctx.closes(24)) < 24:
            return None

        # Detect decisive daily direction at month boundary
        # We need to look at the daily change *around* the month transition
        # For simplicity, let's look at the daily change ending at the current bar
        daily_change = self._get_daily_change(ctx.closes(24), 24)

        if ctx.has_position():
            # If we are in a position, we only look to exit based on time or TP/SL
            # For this strategy, we might want to exit after the month turn window has passed
            # or if the initial move has reversed significantly.
            # Let's keep it simple: exit if the month turn window has passed AND we are profitable
            # or if it's been a long time.
            if ctx.position_direction() == "long":
                if ctx.bar_index - ctx.position["entry_bar_index"] > self.METADATA["declared_hold_seconds"] / 3600:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_hold_time_exceeded", "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]})
                # If we are long and the price starts to fall significantly after the entry
                if ctx.bar.close < ctx.position["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000 * 0.5): # half SL as soft exit
                    return ctx.signal("flat", confidence=0.4, metadata={"reason": "long_soft_stop_loss", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
            elif ctx.position_direction() == "short":
                if ctx.bar_index - ctx.position["entry_bar_index"] > self.METADATA["declared_hold_seconds"] / 3600:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_hold_time_exceeded", "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]})
                # If we are short and the price starts to rise significantly after the entry
                if ctx.bar.close > ctx.position["entry_price"] * (1 + self.METADATA["declared_sl_bps"] / 10000 * 0.5): # half SL as soft exit
                    return ctx.signal("flat", confidence=0.4, metadata={"reason": "short_soft_stop_loss", "entry_price": ctx.position["entry_price"], "current_price": ctx.bar.close})
            return None # Hold existing position

        # Entry logic
        signal = None
        if daily_change > self.min_daily_move_pct:
            # Strong bullish move
            signal = ctx.signal(
                "long",
                confidence=0.7,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "month_turn_bullish_momentum",
                    "daily_change_pct": daily_change * 100,
                    "current_price": ctx.bar.close,
                    "timestamp": str(ctx.bar.timestamp)
                }
            )
        elif daily_change < -self.min_daily_move_pct:
            # Strong bearish move
            signal = ctx.signal(
                "short",
                confidence=0.7,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "month_turn_bearish_momentum",
                    "daily_change_pct": daily_change * 100,
                    "current_price": ctx.bar.close,
                    "timestamp": str(ctx.bar.timestamp)
                }
            )

        if signal:
            self.last_trade_bar = ctx.bar_index
            return signal

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to ensure next trade respects it
        self.last_trade_bar = ctx.bar_index
        pass # No adaptive logic for now