from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolOvernightHandoff(Strategy):
    METADATA = {
        "name": "SOL Overnight Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Roughly 6-hour hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Apply Wilder's smoothing
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + self.rsi_period)
        highs = ctx.highs(self.long_ema_period + self.rsi_period)
        lows = ctx.lows(self.long_ema_period + self.rsi_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Cooldown logic: do not trade if a position was closed recently
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position():
                return None # Hold current position
            else:
                return None # Still in cooldown, do not open new position

        # Exit existing position logic
        if ctx.has_position():
            # Close position before the next US session (13-17 UTC, roughly 8am-12pm ET)
            # This is a hard boundary exit regardless of profit/loss
            if ctx.position_direction() == "long" and current_hour >= 13 and current_hour <= 17:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_handoff_exit", "exit_hour": current_hour})
            elif ctx.position_direction() == "short" and current_hour >= 13 and current_hour <= 17:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_handoff_exit", "exit_hour": current_hour})
            return None # Hold position if not exiting

        # Entry logic: Only consider entries during overnight UTC (0-6 UTC)
        if not (0 <= current_hour <= 6):
            return None

        # Calculate indicators
        short_ema = self._ema(closes, self.short_ema_period)
        long_ema = self._ema(closes, self.long_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if short_ema is None or long_ema is None or rsi is None:
            return None

        # Get funding rate for additional context/filter
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long Entry Conditions
        # Price crossing above short EMA, short EMA above long EMA (bullish trend confirmation)
        # RSI oversold condition or neutral
        # Positive funding rate as a filter (traders paying to be long, indicating bullish sentiment)
        if closes[-2] < short_ema and closes[-1] > short_ema: # Current close crossed above short EMA
            if short_ema > long_ema: # Short EMA above Long EMA
                if rsi < 50: # Not overbought, potentially oversold or neutral
                    if funding_rate > 0.00005: # Positive funding rate filter
                        signal_direction = "long"
                        confidence = min(0.7 + (ctx.rng.uniform(0,1) * 0.2), 1.0) # Add a touch of randomness
                        reason = "ema_cross_overnight_long_entry"
                        self.last_trade_bar = ctx.bar_index

        # Short Entry Conditions
        # Price crossing below short EMA, short EMA below long EMA (bearish trend confirmation)
        # RSI overbought condition or neutral
        # Negative funding rate as a filter (traders paying to be short, indicating bearish sentiment)
        if closes[-2] > short_ema and closes[-1] < short_ema: # Current close crossed below short EMA
            if short_ema < long_ema: # Short EMA below Long EMA
                if rsi > 50: # Not oversold, potentially overbought or neutral
                    if funding_rate < -0.00005: # Negative funding rate filter
                        signal_direction = "short"
                        confidence = min(0.7 + (ctx.rng.uniform(0,1) * 0.2), 1.0)
                        reason = "ema_cross_overnight_short_entry"
                        self.last_trade_bar = ctx.bar_index

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "short_ema": short_ema,
                    "long_ema": long_ema,
                    "rsi": rsi,
                    "funding_rate_solusdt": funding_rate,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position close
        self.last_trade_bar = ctx.bar_index
        pass