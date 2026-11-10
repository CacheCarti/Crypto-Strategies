from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionImpulseCarry(Strategy):
    METADATA = {
        "name": "US Session Impulse Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold, exit before midnight UTC
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 18 # UTC 18:00
        self.us_session_end_hour = 24   # UTC 00:00 (end of day)
        self.cooldown_bars = 5          # Cooldown after a trade
        self.bars_since_last_trade = self.cooldown_bars + 1
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.entry_triggered_today = False
        self.ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        self.bars_since_last_trade += 1

        # Reset session data at the start of the US session
        if current_hour == self.us_session_start_hour and self.session_open_price is None:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.entry_triggered_today = False # Reset daily entry flag
        elif current_hour != self.us_session_start_hour and self.session_open_price is None and not self.entry_triggered_today:
             # If we missed the exact open of the session, but are in it, initialize
            if current_hour > self.us_session_start_hour and current_hour < self.us_session_end_hour:
                # Find the open price from the first bar of the session, if available
                if len(ctx.opens(current_hour - self.us_session_start_hour + 1)) > current_hour - self.us_session_start_hour:
                    self.session_open_price = ctx.opens(current_hour - self.us_session_start_hour + 1)[0]
                else:
                    self.session_open_price = ctx.bar.open # Fallback if not enough history for session open
                self.session_high = max(ctx.highs(current_hour - self.us_session_start_hour + 1))
                self.session_low = min(ctx.lows(current_hour - self.us_session_start_hour + 1))


        # Update session high/low if within the session
        if self.session_open_price is not None and current_hour >= self.us_session_start_hour and current_hour < self.us_session_end_hour:
            self.session_high = max(self.session_high, current_high)
            self.session_low = min(self.session_low, current_low)

        # Calculate EMA for trend confirmation
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Feature: Fear & Greed Index for sentiment filter
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        sentiment_is_bullish = fear_greed_index > 50
        sentiment_is_bearish = fear_greed_index < 50

        # --- Position Management ---
        if ctx.has_position():
            # Exit logic: close position at the end of the US session (UTC 00:00)
            if current_hour == self.us_session_end_hour -1: # Close on the last bar of the US session
                if ctx.position_direction() == "long":
                    reason = "end_of_session_exit_long"
                else:
                    reason = "end_of_session_exit_short"
                return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_hour": current_hour, "session_open": self.session_open_price, "current_close": current_close})

            # Hold the position
            return ctx.signal("hold")

        # --- Entry Logic ---
        # Only consider entries if not in cooldown and not already entered today
        if self.bars_since_last_trade <= self.cooldown_bars or self.entry_triggered_today:
            return None

        # Entry within the US session (UTC 18-24)
        if current_hour >= self.us_session_start_hour and current_hour < self.us_session_end_hour - 1: # Don't enter on the very last bar of the session
            if self.session_open_price is not None:
                # Impulse detection: price has moved significantly from session open
                impulse_threshold_bps = 50.0 # 0.5% move from session open
                impulse_up = (current_close - self.session_open_price) / self.session_open_price * 10000
                impulse_down = (self.session_open_price - current_close) / self.session_open_price * 10000

                # Confirmation: current close is above/below EMA
                is_above_ema = current_close > ema
                is_below_ema = current_close < ema

                # Long Entry: Strong impulse up, confirmed by EMA and bullish sentiment
                if impulse_up > impulse_threshold_bps and is_above_ema and sentiment_is_bullish:
                    self.entry_triggered_today = True
                    self.bars_since_last_trade = 0
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "us_session_long_impulse_ema_confirm_bull_sentiment",
                            "session_open": self.session_open_price,
                            "current_close": current_close,
                            "impulse_bps": impulse_up,
                            "ema": ema,
                            "fear_greed_index": fear_greed_index,
                            "current_hour": current_hour
                        }
                    )

                # Short Entry: Strong impulse down, confirmed by EMA and bearish sentiment
                if impulse_down > impulse_threshold_bps and is_below_ema and sentiment_is_bearish:
                    self.entry_triggered_today = True
                    self.bars_since_last_trade = 0
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "us_session_short_impulse_ema_confirm_bear_sentiment",
                            "session_open": self.session_open_price,
                            "current_close": current_close,
                            "impulse_bps": impulse_down,
                            "ema": ema,
                            "fear_greed_index": fear_greed_index,
                            "current_hour": current_hour
                        }
                    )

        # End of session, reset session tracking for the next day
        if current_hour == self.us_session_end_hour -1: # This is the last bar of the US session, before 00:00 UTC
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.entry_triggered_today = False # Prepare for next day's entry

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown and session tracking on explicit position close
        self.bars_since_last_trade = 0
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.entry_triggered_today = False
        # Optional: log position details for analysis
        # ctx.log(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']} bps return.")