from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthUsSessionHandoff(Strategy):
    METADATA = {
        "name": "ETH US Session Handoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Up to 4 hours, crossing session
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 13  # UTC 13:00 (US session open)
        self.session_end_hour = 21    # UTC 21:00 (US session close)
        self.lookback_period = 8      # Look back 8 hours for session impulse
        self.cooldown_bars = 6        # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # State to track if we've already traded in the current session
        self.traded_this_session = False
        self.current_session_day = -1

        # State to detect transition for faster trigger
        self.prev_session_impulse = 0.0 # 1 for up, -1 for down, 0 for neutral

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        opens = ctx.opens(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset traded_this_session flag at the start of a new day's US session
        if current_day != self.current_session_day and current_hour == self.session_start_hour:
            self.traded_this_session = False
            self.current_session_day = current_day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic for entering a position during the US session
        if current_hour >= self.session_start_hour and current_hour < self.session_end_hour:
            if not ctx.has_position() and not self.traded_this_session:
                # Calculate session impulse
                session_open_price = opens[0] # Open of the first bar in the lookback
                session_close_price = closes[-1] # Close of the current bar

                # Simple directional impulse: price change over the lookback period
                price_change = session_close_price - session_open_price
                
                # Use a simple moving average to filter noise and detect trend
                sma_period = self.lookback_period // 2 # e.g., 4-hour SMA
                if len(closes) < sma_period:
                    return None
                
                current_sma = self._sma(closes, sma_period)
                if current_sma is None:
                    return None

                # Faster trigger variant: detect transition in impulse
                current_impulse_direction = 0
                if price_change > 0 and closes[-1] > current_sma:
                    current_impulse_direction = 1 # Bullish
                elif price_change < 0 and closes[-1] < current_sma:
                    current_impulse_direction = -1 # Bearish

                # Only trigger if impulse direction has just changed or is strong and sustained
                # This prevents firing every bar if conditions are met
                if current_impulse_direction != 0 and current_impulse_direction != self.prev_session_impulse:
                    confidence = min(0.6 + abs(price_change / session_open_price) * 10, 0.9) # Scale confidence by magnitude
                    
                    signal_direction = None
                    reason = ""
                    
                    if current_impulse_direction == 1: # Bullish impulse
                        signal_direction = "long"
                        reason = "us_session_bullish_impulse_transition"
                    elif current_impulse_direction == -1: # Bearish impulse
                        signal_direction = "short"
                        reason = "us_session_bearish_impulse_transition"

                    if signal_direction:
                        self.traded_this_session = True
                        self.last_trade_bar = ctx.bar_index
                        self.prev_session_impulse = current_impulse_direction
                        return ctx.signal(
                            signal_direction,
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": reason,
                                "session_open_price": session_open_price,
                                "current_price": closes[-1],
                                "price_change_pct": (price_change / session_open_price) * 100,
                                "sma_value": current_sma,
                                "impulse_direction": current_impulse_direction
                            }
                        )
                self.prev_session_impulse = current_impulse_direction # Update for next bar

        # Logic for exiting a position before the session end
        if ctx.has_position() and current_hour >= self.session_end_hour:
            self.last_trade_bar = ctx.bar_index # Reset cooldown on exit
            self.prev_session_impulse = 0.0 # Reset impulse on session end
            return ctx.signal(
                "flat",
                confidence=0.7,
                metadata={
                    "reason": "session_end_exit",
                    "exit_hour": current_hour,
                    "current_price": ctx.bar.close,
                    "entry_price": ctx.position["entry_price"]
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown and impulse on any position close
        self.last_trade_bar = ctx.bar_index
        self.prev_session_impulse = 0.0
        # Optional: adaptive logic based on position["return_bps"]
        # For example, if position["return_bps"] < -100, increase cooldown_bars
        pass