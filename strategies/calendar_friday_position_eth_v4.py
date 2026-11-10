from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningETH(Strategy):
    METADATA = {
        "name": "FridayPositioningETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 24, # Need enough bars for 6-hour lookback
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -99999
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.min_move_pct = 0.005 # 0.5% decisive move in 6 hours

    def _calculate_6hr_move(self, closes):
        if len(closes) < 6:
            return None
        start_price = closes[-6]
        end_price = closes[-1]
        return (end_price - start_price) / start_price

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Need 24 for potential future indicator if added
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Regime filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Only consider trades on Friday late afternoon/evening UTC
        # Friday (4), 18:00 UTC to 23:00 UTC
        is_friday_entry_window = (current_weekday == 4 and current_hour >= 18 and current_hour <= 23)

        # Only consider trades on Sunday early morning UTC for potential reversal/continuation
        # Sunday (6), 00:00 UTC to 05:00 UTC
        is_sunday_entry_window = (current_weekday == 6 and current_hour >= 0 and current_hour <= 5)

        if not (is_friday_entry_window or is_sunday_entry_window):
            return None

        # Calculate decisive move over the last 6 hours
        six_hr_move = self._calculate_6hr_move(closes)
        if six_hr_move is None:
            return None

        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        metadata_vals = {"six_hr_move": six_hr_move * 100, "current_hour": current_hour, "weekday": current_weekday}

        # Friday Positioning: Look for strong moves that suggest positioning for the weekend
        if is_friday_entry_window:
            # Bullish setup: Strong upward move on Friday, not in a bear trend, and not in crisis
            if six_hr_move > self.min_move_pct and trend_regime != "bear" and regime not in ["CRISIS", "MELTDOWN"]:
                signal_direction = "long"
                confidence = max(0.5, min(0.9, abs(six_hr_move) * 100 * 0.1)) # Scale confidence by move size
                reason = "friday_positive_positioning"
            # Bearish setup: Strong downward move on Friday, not in a bull trend, and not in crisis
            elif six_hr_move < -self.min_move_pct and trend_regime != "bull" and regime not in ["CRISIS", "MELTDOWN"]:
                signal_direction = "short"
                confidence = max(0.5, min(0.9, abs(six_hr_move) * 100 * 0.1))
                reason = "friday_negative_positioning"

        # Sunday Positioning: Look for continuation or reversal after weekend
        elif is_sunday_entry_window:
            # If there was a strong positive move on Sunday early hours, and trend is not bear
            if six_hr_move > self.min_move_pct and trend_regime != "bear":
                signal_direction = "long"
                confidence = max(0.5, min(0.8, abs(six_hr_move) * 100 * 0.1))
                reason = "sunday_early_positive_continuation"
            # If there was a strong negative move on Sunday early hours, and trend is not bull
            elif six_hr_move < -self.min_move_pct and trend_regime != "bull":
                signal_direction = "short"
                confidence = max(0.5, min(0.8, abs(six_hr_move) * 100 * 0.1))
                reason = "sunday_early_negative_continuation"

        if signal_direction and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            metadata_vals.update({"reason": reason, "regime": regime, "trend_regime": trend_regime})
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata_vals
            )
        elif ctx.has_position() and signal_direction and ctx.position_direction() != signal_direction:
            # If we have an opposite position and a new signal, flip it
            self.last_trade_bar = ctx.bar_index
            metadata_vals.update({"reason": f"flip_from_{ctx.position_direction()}_to_{signal_direction}", "regime": regime, "trend_regime": trend_regime})
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata_vals
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy based on previous position outcome
        pass