from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionImpulseCarry(Strategy):
    METADATA = {
        "name": "US Session Impulse Carry",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar, might hold for 1-2 bars
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC 13:00
        self.us_session_end_hour = 21   # UTC 21:00 (exit before this hour)
        self.entry_hour_long = 14       # Enter long between 14:00 and 15:00 UTC
        self.entry_hour_short = 15      # Enter short between 15:00 and 16:00 UTC
        self.cooldown_bars = 3          # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

        self.impulse_period = 4 # Number of bars to measure impulse in US session
        self.sma_period = 20
        self.rsi_period = 14

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold")
            else:
                return None # Still in cooldown, don't enter

        # Exit logic: Close all positions before US session end hour
        if ctx.has_position() and current_hour >= self.us_session_end_hour:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "end_of_session_exit",
                    "current_hour": current_hour,
                    "exit_price": current_close,
                }
            )

        # Entry logic: Only try to enter during specific hours within the US session
        if ctx.has_position():
            return ctx.signal("hold") # Hold existing position

        # Calculate indicators
        sma = self._sma(closes, self.sma_period)
        rsi = self._rsi(closes, self.rsi_period)
        if sma is None or rsi is None:
            return None

        # Measure US session impulse for long entry
        if current_hour == self.entry_hour_long:
            # Check impulse: last 'impulse_period' bars show upward momentum
            if len(closes) >= self.impulse_period:
                impulse_closes = closes[-self.impulse_period:]
                # Check if the last close is higher than the open of the impulse period
                # and if the overall trend during this period is positive
                if impulse_closes[-1] > impulse_closes[0] and current_close > sma:
                    # Additional filter: RSI not overbought, funding rate positive
                    funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
                    if rsi < 70 and funding_rate > 0:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "us_session_long_impulse_carry",
                                "entry_hour": current_hour,
                                "current_close": current_close,
                                "sma": sma,
                                "rsi": rsi,
                                "funding_rate": funding_rate,
                                "impulse_start_price": impulse_closes[0],
                                "impulse_end_price": impulse_closes[-1],
                            }
                        )

        # Measure US session impulse for short entry
        elif current_hour == self.entry_hour_short:
            # Check impulse: last 'impulse_period' bars show downward momentum
            if len(closes) >= self.impulse_period:
                impulse_closes = closes[-self.impulse_period:]
                # Check if the last close is lower than the open of the impulse period
                # and if the overall trend during this period is negative
                if impulse_closes[-1] < impulse_closes[0] and current_close < sma:
                    # Additional filter: RSI not oversold, funding rate negative
                    funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
                    if rsi > 30 and funding_rate < 0:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "us_session_short_impulse_carry",
                                "entry_hour": current_hour,
                                "current_close": current_close,
                                "sma": sma,
                                "rsi": rsi,
                                "funding_rate": funding_rate,
                                "impulse_start_price": impulse_closes[0],
                                "impulse_end_price": impulse_closes[-1],
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters if needed
        pass