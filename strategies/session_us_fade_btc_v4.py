from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionUSFadeBTC(Strategy):
    METADATA = {
        "name": "SessionUSFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold time, up to 4 hours within session
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, relies on OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_midpoint_period = 4  # Bars for session midpoint calculation
        self.stretch_period = 6          # Bars to determine "stretched" move (e.g., 6 hours)
        self.cooldown_bars = 5           # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.entry_bar_index = -1
        self.entry_price = 0.0
        self.session_entry_hour = -1 # To ensure only one entry per session

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.stretch_period + 1)
        highs = ctx.highs(self.stretch_period + 1)
        lows = ctx.lows(self.stretch_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # US Session is roughly 13:00 UTC to 21:00 UTC (9 hours total)
        # Entry window: 17:00 UTC to 19:00 UTC (middle of US session)
        # Exit before 21:00 UTC
        is_us_session_entry_window = 17 <= current_hour < 19
        is_us_session_active = 13 <= current_hour < 21
        is_us_session_close_window = current_hour >= 20

        # Close position if session is ending
        if ctx.has_position() and is_us_session_close_window:
            self.last_trade_bar = ctx.bar_index
            self.session_entry_hour = -1 # Reset for next session
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_end_time",
                "current_hour": current_hour,
                "entry_price": ctx.position["entry_price"]
            })
            
        # If we have a position, just hold unless it's time to close
        if ctx.has_position():
            return None

        # Only one entry per session
        if self.session_entry_hour == current_hour:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Do not trade in crisis or strong trends
        if crisis_score > 0.5:
            return None
        if trend_regime != "neutral" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None

        # Entry conditions
        if is_us_session_entry_window:
            # Calculate session midpoint
            session_opens = ctx.opens(self.session_midpoint_period)
            session_closes = ctx.closes(self.session_midpoint_period)
            if len(session_opens) < self.session_midpoint_period or len(session_closes) < self.session_midpoint_period:
                return None
            
            # Simple average of opens and closes for midpoint estimation
            session_midpoint = (session_opens[0] + session_closes[-1]) / 2.0

            # Calculate the "stretch" of the current move
            # Using the range of highs/lows and comparing to current price vs midpoint
            recent_high = max(highs[-self.stretch_period:])
            recent_low = min(lows[-self.stretch_period:])
            
            price_range = recent_high - recent_low
            
            if price_range == 0: # Avoid division by zero
                return None

            # How far current price is from the session midpoint, normalized by recent range
            deviation_from_midpoint = (ctx.bar.close - session_midpoint) / price_range
            
            # Look for "stretched" conditions: price far from midpoint
            # and potentially stalling (e.g., closing back towards midpoint)
            
            # Fade long: price is significantly above midpoint, and current bar is moving down
            if deviation_from_midpoint > 0.5 and ctx.bar.close < ctx.bar.open: # Stretched up, current bar selling
                self.last_trade_bar = ctx.bar_index
                self.session_entry_hour = current_hour
                self.entry_price = ctx.bar.close
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_fade_stretched_up",
                        "current_hour": current_hour,
                        "session_midpoint": session_midpoint,
                        "deviation_from_midpoint": deviation_from_midpoint,
                        "close_vs_open": "down",
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

            # Fade short: price is significantly below midpoint, and current bar is moving up
            if deviation_from_midpoint < -0.5 and ctx.bar.close > ctx.bar.open: # Stretched down, current bar buying
                self.last_trade_bar = ctx.bar_index
                self.session_entry_hour = current_hour
                self.entry_price = ctx.bar.close
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_fade_stretched_down",
                        "current_hour": current_hour,
                        "session_midpoint": session_midpoint,
                        "deviation_from_midpoint": deviation_from_midpoint,
                        "close_vs_open": "up",
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session entry hour to allow new entries in subsequent sessions
        self.session_entry_hour = -1
        # Log position close for analysis
        # print(f"Position closed: {position['direction']} at {position['exit_price']} (Entry: {position['entry_price']})")
        # print(f"Return: {position['return_bps']} bps, Held: {position['bars_held']} bars")
        pass