from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthLateUsSessionDrift(Strategy):
    METADATA = {
        "name": "EthLateUsSessionDrift",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 48, # For 24-bar range and 24-bar median
        "required_features": []
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18 # UTC 18:00 (late US session)
        self.session_end_hour = 24   # UTC 00:00 (end of day)
        self.range_period = 24       # Lookback for median range
        self.min_move_multiplier = 1.0 # Minimum move as multiple of median range
        self.max_move_multiplier = 3.0 # Max move to avoid exhaustion
        self.cooldown_bars = 4       # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade
        self.traded_today = False # Track if we traded in the current UTC day
        self.min_volatility_regime = "normal" # Only trade in normal or higher volatility
        self.allowed_trend_regimes = ["bull", "neutral"] # Only trade in bull or neutral trends

    def _median(self, values):
        if not values:
            return 0
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period * 2) # Need more for range calculation
        highs = ctx.highs(self.range_period * 2)
        lows = ctx.lows(self.range_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade flag at the start of a new UTC day
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False # Reset for the new day
        elif current_hour != 0 and self.traded_today and ctx.bar.timestamp.hour == 0:
            # This handles the transition from 23:xx to 00:xx
            self.traded_today = False

        # Exit existing position if it's past session end or if conditions change
        if ctx.has_position():
            # Exit before UTC 0:00 if position is still open
            if current_hour >= self.session_end_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # Simple structure stop: if price reverses significantly against entry
            if ctx.position_direction() == "long" and ctx.bar.close < ctx.position["entry_price"] * (1 - 0.005): # 0.5% below entry
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_long", "entry_price": ctx.position["entry_price"], "close": ctx.bar.close})
            if ctx.position_direction() == "short" and ctx.bar.close > ctx.position["entry_price"] * (1 + 0.005): # 0.5% above entry
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "structure_stop_short", "entry_price": ctx.position["entry_price"], "close": ctx.bar.close})

            return None # Hold position

        # Entry logic
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None # Only consider entries during the late US session

        if ctx.bar_index - self.last_trade_bar <= self.cooldown_bars:
            return None # Cooldown active

        if self.traded_today:
            return None # Only one entry per day

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL").lower()
        if market_regime not in ["normal", "high_vol"]: # Only trade in normal or high volatility
            return None
        
        trend_regime = ctx.market.get("trend_regime", "neutral").lower()
        if trend_regime not in self.allowed_trend_regimes:
            return None

        # Calculate historical daily ranges
        daily_ranges = []
        for i in range(len(highs) - self.range_period, len(highs) - 1):
            if i >= 0:
                daily_ranges.append(highs[i] - lows[i])
        
        if not daily_ranges:
            return None

        median_daily_range = self._median(daily_ranges)
        if median_daily_range == 0:
            return None

        # Calculate session move
        session_high = max(highs[-self.range_period:])
        session_low = min(lows[-self.range_period:])
        session_open = closes[-self.range_period] if len(closes) >= self.range_period else closes[0]

        current_session_move_abs = abs(ctx.bar.close - session_open)
        current_session_move_pct = (ctx.bar.close - session_open) / session_open

        # Avoid single-bar spikes: check if the move was primarily on the current bar
        if len(closes) >= 2:
            prev_bar_move_abs = abs(closes[-1] - session_open)
            if current_session_move_abs > (prev_bar_move_abs * 2) and current_session_move_abs > median_daily_range * 0.5:
                # If current bar move is disproportionately large compared to previous session move, it's a spike
                return None


        min_move_threshold = median_daily_range * self.min_move_multiplier
        max_move_threshold = median_daily_range * self.max_move_multiplier

        if min_move_threshold < current_session_move_abs < max_move_threshold:
            confidence = 0.6 + (current_session_move_abs / max_move_threshold) * 0.3 # Scale confidence

            if current_session_move_pct > 0.005: # Decisive upward move (0.5%)
                self.last_trade_bar = ctx.bar_index
                self.traded_today = True
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "late_us_session_drift_long",
                        "session_move_pct": current_session_move_pct,
                        "session_move_abs": current_session_move_abs,
                        "median_range": median_daily_range,
                        "current_hour": current_hour,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime
                    }
                )
            elif current_session_move_pct < -0.005: # Decisive downward move (0.5%)
                self.last_trade_bar = ctx.bar_index
                self.traded_today = True
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "late_us_session_drift_short",
                        "session_move_pct": current_session_move_pct,
                        "session_move_abs": current_session_move_abs,
                        "median_range": median_daily_range,
                        "current_hour": current_hour,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position outcome
        pass