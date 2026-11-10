from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionImpulse(Strategy):
    METADATA = {
        "name": "Asia Session Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-8 hour holds
        "warmup_bars": 24, # Need enough bars for session high/low
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_signal_bar = -1
        self.cooldown_bars = 4 # Wait 4 bars (4 hours) after a trade
        self.session_high = -1.0
        self.session_low = -1.0
        self.session_open = -1.0
        self.session_close = -1.0
        self.session_direction = "neutral"
        self.session_start_bar = -1
        self.session_evaluated = False # Flag to ensure evaluation only once per session
        self.last_trade_direction = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Asia session (UTC 0-8) logic
        # Reset session data at UTC 0 (start of Asia session)
        if current_hour == 0 and not self.session_evaluated:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
            self.session_close = ctx.bar.close
            self.session_direction = "neutral"
            self.session_start_bar = ctx.bar_index
            self.session_evaluated = True # Mark as evaluated for this session
        elif current_hour > 0 and current_hour < 8: # During Asia session, update high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_close = ctx.bar.close
        elif current_hour == 8 and self.session_evaluated: # End of Asia session, evaluate direction
            # Determine Asia session direction
            if self.session_close > self.session_open:
                self.session_direction = "long"
            elif self.session_close < self.session_open:
                self.session_direction = "short"
            else:
                self.session_direction = "neutral"
            self.session_evaluated = False # Reset for next session

        # Trading logic (after Asia session closes, looking for follow-through)
        # Entry opportunity in London/Europe session (UTC 8-16)
        if current_hour >= 8 and current_hour < 16:
            # Check cooldown
            if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
                return None

            if not ctx.has_position():
                # Long entry: Asia session was bullish and current price breaks above Asia high
                if self.session_direction == "long" and ctx.bar.close > self.session_high:
                    # Additional filter: ensure current bar is strong
                    if ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.open) / ctx.bar.open > 0.001: # 0.1% positive close
                        self.last_signal_bar = ctx.bar_index
                        self.last_trade_direction = "long"
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_bullish_breakout",
                                "asia_session_open": self.session_open,
                                "asia_session_close": self.session_close,
                                "asia_session_high": self.session_high,
                                "current_close": ctx.bar.close,
                                "current_hour": current_hour,
                            }
                        )

                # Short entry: Asia session was bearish and current price breaks below Asia low
                elif self.session_direction == "short" and ctx.bar.close < self.session_low:
                    # Additional filter: ensure current bar is strong
                    if ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) / ctx.bar.open > 0.001: # 0.1% negative close
                        self.last_signal_bar = ctx.bar_index
                        self.last_trade_direction = "short"
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "asia_session_bearish_breakout",
                                "asia_session_open": self.session_open,
                                "asia_session_close": self.session_close,
                                "asia_session_low": self.session_low,
                                "current_close": ctx.bar.close,
                                "current_hour": current_hour,
                            }
                        )

        # Exit logic: Close all positions before UTC 16 (end of London/Europe session)
        if ctx.has_position() and current_hour >= 15: # Signal flat at 15:00 UTC to close before 16:00 UTC bar
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "session_end_time_exit",
                    "current_hour": current_hour,
                    "entry_direction": ctx.position_direction(),
                    "entry_price": ctx.position["entry_price"],
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session state for the next trading day/cycle if needed
        # Or use this for adaptive sizing / logging
        pass