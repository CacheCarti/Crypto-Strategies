from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionImpulse(Strategy):
    METADATA = {
        "name": "SOL_US_Session_Impulse",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 20,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13
        self.us_session_end_hour = 21 # Exit before this hour
        self.session_impulse_period = 4 # Bars to measure impulse (e.g., 4 hours)
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_impulse_period + 1)
        opens = ctx.opens(self.session_impulse_period + 1)
        
        if len(closes) < self.session_impulse_period + 1 or len(opens) < self.session_impulse_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit existing position if it's time
        if ctx.has_position():
            if current_hour >= self.us_session_end_hour:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position during session

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Only consider entering at the start of the US session, or shortly after
        if current_hour == self.us_session_start_hour + 1: # Entry one hour after session start to confirm impulse
            # Calculate session impulse
            session_open_price = opens[-(self.session_impulse_period + 1)] # Open of the first bar of the impulse period
            session_current_price = closes[-1]
            
            # Simple impulse: current price vs session open price
            price_change = session_current_price - session_open_price
            
            # Check for significant impulse (e.g., 0.5% change)
            impulse_threshold_pct = 0.005 # 0.5%
            
            if price_change / session_open_price > impulse_threshold_pct:
                # Bullish impulse
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long", 
                    confidence=0.7, 
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_bullish_impulse",
                        "session_open": session_open_price,
                        "current_price": session_current_price,
                        "price_change_pct": (price_change / session_open_price) * 100
                    }
                )
            elif price_change / session_open_price < -impulse_threshold_pct:
                # Bearish impulse
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short", 
                    confidence=0.7, 
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_bearish_impulse",
                        "session_open": session_open_price,
                        "current_price": session_current_price,
                        "price_change_pct": (price_change / session_open_price) * 100
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Record the last trade bar index to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        pass