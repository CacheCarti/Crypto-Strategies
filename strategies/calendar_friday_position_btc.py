from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningBTC(Strategy):
    METADATA = {
        "name": "FridayPositioningBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, allowing for some weekend drift
        "warmup_bars": 24, # Need enough bars for 6-hour lookback
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Ensure it can trade on first Friday
        self.friday_entry_hour_utc = 20 # 8 PM UTC Friday
        self.friday_exit_hour_utc = 23 # 11 PM UTC Friday (exit before Sunday open)
        self.min_decisive_move_pct = 0.2 # Minimum 0.2% move over 6 hours to be considered decisive

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(24) # Need 24 bars for 6-hour lookback (6 * 4 = 24 if 15m, but 6 for 1h)
        if len(closes) < 6: # Need at least 6 hours of data for the decisive move check
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Entry logic: Late Friday
        if current_weekday == 4 and current_hour == self.friday_entry_hour_utc: # Friday 8 PM UTC
            if ctx.has_position():
                return None

            # Check for decisive move in the last 6 hours
            price_6_hours_ago = closes[-6]
            current_price = ctx.bar.close
            price_change_pct = (current_price - price_6_hours_ago) / price_6_hours_ago * 100

            if price_change_pct > self.min_decisive_move_pct:
                # Decisive upward move, go long
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_positioning_long",
                        "entry_hour_utc": current_hour,
                        "price_change_6h_pct": round(price_change_pct, 2),
                        "price_6h_ago": round(price_6_hours_ago, 2),
                        "current_price": round(current_price, 2),
                    }
                )
            elif price_change_pct < -self.min_decisive_move_pct:
                # Decisive downward move, go short
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_positioning_short",
                        "entry_hour_utc": current_hour,
                        "price_change_6h_pct": round(price_change_pct, 2),
                        "price_6h_ago": round(price_6_hours_ago, 2),
                        "current_price": round(current_price, 2),
                    }
                )

        # Exit logic: Before Sunday open, if still in position
        if ctx.has_position() and current_weekday == 4 and current_hour == self.friday_exit_hour_utc: # Friday 11 PM UTC
            # Exit any open position before the weekend truly sets in
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "friday_positioning_weekend_exit",
                    "exit_hour_utc": current_hour,
                    "position_direction": ctx.position_direction(),
                    "entry_price": round(ctx.position["entry_price"], 2),
                    "current_price": round(ctx.bar.close, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']}. Return: {position['return_bps']} bps. Bars held: {position['bars_held']}. Cooldown initiated.")