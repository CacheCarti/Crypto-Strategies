```python
from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "Asia Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,       # Stop-loss in bps (e.g., 2% from entry)
        "declared_tp_bps": 300.0,       # Take-profit in bps (e.g., 3% from entry)
        "declared_hold_seconds": 7200,  # Intended avg hold time (2 hours for 1h bars)
        "warmup_bars": 30,              # Bars before first signal (to ensure enough history for market context)
        "required_features": []         # No specific features needed, using market context
    }

    # Constants for the strategy
    ASIA_SESSION_START_HOUR = 0  # UTC 0:00
    ASIA_SESSION_END_HOUR = 8    # Exit before this hour (i.e., exit on the 7:00-8:00 UTC bar)
    STRETCH_THRESHOLD_PCT = 0.3  # Percentage move from session open to consider "stretched"
    COOLDOWN_PERIOD = 5          # Bars to wait after a trade closes before entering again

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_price = 0.0
        self.session_high = 0.0
        self.session_low = float('inf')
        self.traded_this_session = False
        self.last_session_day = -1  # To detect a new day/session
        self.cooldown_bars = 0      # Cooldown counter

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- 1. Session Management ---
        # Detect a new day (UTC 0:00 bar) or the very first bar of the backtest
        is_first_bar_of_backtest = (ctx.bar_index == 0)
        is_new_day_or_session_start = (current_day != self.last_session_day)

        if is_first_bar_of_backtest or is_new_day_or_session_start:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.traded_this_session = False
            self.last_session_day = current_day
        else:
            # Update session high/low for the current session
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Ensure we have a valid session_open_price before proceeding with logic
        if self.session_open_price == 0.0:
            return None

        # --- 2. Cooldown Check ---
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # --- 3. Exit Logic (End of Asia Session) ---
        # If a position is open and it's the last bar of the Asia session (7:00-8:00 UTC)
        if ctx.has_position() and current_hour == (self.ASIA_SESSION_END_HOUR - 1):
            self.cooldown_bars = self.COOLDOWN_PERIOD
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_end_exit",
                "current_hour": current_hour,
                "position_direction": ctx.position_direction(),
                "session_open": self.session_open_price,
                "current_price": ctx.bar.close
            })

        # --- 4. Entry Logic (within Asia Session, UTC 0-7) ---
        # Only consider entries during the Asia session and if no trade has been taken yet
        if self.ASIA_SESSION_START_HOUR <= current_hour < self.ASIA_SESSION_END_HOUR and not self.traded_this_session:
            # Wait for at least two full bars into the session to establish a pattern
            if current_hour < self.ASIA_SESSION_START_HOUR + 2:
                return None

            current_price = ctx.bar.close
            move_from_open = current_price - self.session_open_price
            percent_move_from_open = (move_from_open / self.session_open_price) * 100

            # Regime Filters
            regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")
            crisis_score = ctx.market.get("crisis_score", 0.0)

            # Avoid trading during crisis regimes
            if regime == "CRISIS" or crisis_score > 0.5:
                return None

            # Long Condition: Price stretched down, now showing signs of stalling/reversal
            if percent_move_from_open < -self.STRETCH_THRESHOLD_PCT:
                # Stall/reversal: current bar is green (closing higher than open)
                if ctx.bar.close > ctx.bar.open:
                    # Avoid longing into a strong bear trend and ensure no position is open
                    if trend_regime != "bear" and not ctx.has_position():
                        self.traded_this_session = True
                        self.cooldown_bars = self.COOLDOWN_PERIOD
                        return ctx.signal("long", confidence=0.7,
                                          stop_loss_bps=self.METADATA["declared_sl_bps"],
                                          take_profit_bps=self.METADATA["declared_tp_bps"],
                                          horizon_seconds=self.METADATA["declared_hold_seconds"],
                                          metadata={
                                              "reason": "asia_session_fade_long",
                                              "session_open": round(self.session_open_price, 2),
                                              "current_price": round(current_price, 2),
                                              "percent_move_from_open": round(percent_move_from_open, 2),
                                              "trend_regime": trend_regime,
                                              "regime": regime,
                                              "bar_type": "green_stall_candle"
                                          })

            # Short Condition: Price stretched up, now showing signs of stalling/reversal
            elif percent_move_from_