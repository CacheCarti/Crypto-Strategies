from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionEuropeFadeSOL(Strategy):
    METADATA = {
        "name": "Session Europe Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-hour session hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fade_period = 20  # Lookback for session move
        self.stdev_multiplier = 1.5 # How many stdevs away from mean to consider "stretched"
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM (noon)
        self.exit_hour = 12        # Exit by UTC 12 PM if not stopped out
        self.min_bars_since_trade = 5 # Cooldown period

        self.last_trade_bar_index = -self.min_bars_since_trade - 1 # Initialize to allow immediate trade

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
        closes = ctx.closes(self.fade_period * 2) # Need enough data for SMA and StdDev
        if len(closes) < self.fade_period * 2:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for time-based exit
        if ctx.has_position():
            # If position is open and it's past exit hour, close it
            if current_hour >= self.exit_hour and ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit", "current_hour": current_hour, "exit_hour": self.exit_hour})
            elif current_hour >= self.exit_hour and ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit", "current_hour": current_hour, "exit_hour": self.exit_hour})
            # If position is open, and it's before the exit hour, just hold
            return ctx.signal("hold")


        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.min_bars_since_trade:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Do not trade in crisis or strong trend regimes
        if regime == "CRISIS" or regime == "MELTDOWN":
            return None
        if trend_regime == "bull" or trend_regime == "bear":
            return None # Avoid fading strong trends

        # Entry logic within session hours
        if self.entry_hour_start <= current_hour < self.exit_hour:
            session_closes = closes[-self.fade_period:]
            if len(session_closes) < self.fade_period:
                return None

            sma = self._sma(session_closes, self.fade_period)
            std = self._stddev(session_closes, self.fade_period)

            if sma is None or std is None or std == 0:
                return None

            current_price = ctx.bar.close

            # Check for stretched conditions
            upper_band = sma + self.stdev_multiplier * std
            lower_band = sma - self.stdev_multiplier * std

            signal_confidence = 0.0

            # Fade a stretched move upwards (SELL)
            if current_price > upper_band:
                signal_confidence = min(0.9, (current_price - upper_band) / (std * self.stdev_multiplier))
                if signal_confidence > 0.3: # Minimum confidence to trade
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "session_europe_fade_overbought",
                            "current_price": current_price,
                            "sma_fade_period": sma,
                            "upper_band": upper_band,
                            "std_dev": std,
                            "regime": regime,
                            "trend_regime": trend_regime,
                            "fear_greed": fear_greed,
                            "utc_hour": current_hour
                        }
                    )

            # Fade a stretched move downwards (BUY)
            elif current_price < lower_band:
                signal_confidence = min(0.9, (lower_band - current_price) / (std * self.stdev_multiplier))
                if signal_confidence > 0.3: # Minimum confidence to trade
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "session_europe_fade_oversold",
                            "current_price": current_price,
                            "sma_fade_period": sma,
                            "lower_band": lower_band,
                            "std_dev": std,
                            "regime": regime,
                            "trend_regime": trend_regime,
                            "fear_greed": fear_greed,
                            "utc_hour": current_hour
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # You could use this for adaptive sizing or logging.
        # For this strategy, we just log the trade.
        ctx.log(f"Position closed: {position['direction']} from {position['entry_price']} to {position['exit_price']}. Return: {position['return_bps']:.2f} bps. Held for {position['bars_held']} bars.")
        # self.last_trade_bar_index = ctx.bar_index # Cooldown is handled in on_bar